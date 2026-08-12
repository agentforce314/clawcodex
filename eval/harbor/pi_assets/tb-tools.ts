/**
 * terminal-bench tool pack for pi: `vision_analyze` + `websearch`.
 *
 * pi ships four built-in tools (read, bash, edit, write). Terminal-bench 2.1
 * contains tasks that need to look at an image and tasks that need the live
 * web, and DeepSeek V4 is text-only (`input: ["text"]` in pi's catalogue), so
 * neither is reachable without help. This extension adds exactly the two
 * capabilities clawcodex exposes for the same benchmark — its `vision_analyze`
 * and web-search tools — with the same argument shapes, so trajectories stay
 * comparable across harnesses.
 *
 * Configuration is environment-only (the harbor adapter injects it):
 *
 *   OPENAI_API_KEY / PI_VISION_API_KEY   vision credentials
 *   PI_VISION_MODEL                      default "gpt-5.6-luna"
 *   PI_VISION_BASE_URL                   default "https://api.openai.com/v1"
 *   TAVILY_API_KEY                       web search credentials
 *
 * A missing key is reported as a tool error naming the variable rather than
 * hiding the tool: a silently absent tool reads to the model as "this task is
 * impossible", which is a far more expensive failure than one loud message.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import { extname, resolve } from "node:path";
import { Type } from "typebox";

/** Mirrors clawcodex's `max_result_size_chars` for both tools. */
const MAX_RESULT_CHARS = 50_000;
/** Guard before base64: vendors reject oversized payloads with opaque errors. */
const MAX_IMAGE_BYTES = 16 * 1024 * 1024;
const VISION_TIMEOUT_MS = 60_000;
const SEARCH_TIMEOUT_MS = 20_000;

const DEFAULT_VISION_MODEL = "gpt-5.6-luna";

/** Single source of truth, so the reported `details.model` can't drift from the one used. */
function visionModel(): string {
	return (process.env.PI_VISION_MODEL || DEFAULT_VISION_MODEL).trim();
}

const IMAGE_MIME_TYPES: Record<string, string> = {
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".gif": "image/gif",
	".webp": "image/webp",
	".bmp": "image/bmp",
};

function truncate(text: string): string {
	if (text.length <= MAX_RESULT_CHARS) return text;
	return `${text.slice(0, MAX_RESULT_CHARS)}\n\n[truncated at ${MAX_RESULT_CHARS} characters]`;
}

/** Combine the caller's abort signal with a wall-clock timeout. */
function withTimeout(signal: AbortSignal | undefined, ms: number): { signal: AbortSignal; done: () => void } {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(new Error(`timed out after ${ms}ms`)), ms);
	const forward = () => controller.abort(signal?.reason);
	if (signal) {
		if (signal.aborted) forward();
		else signal.addEventListener("abort", forward, { once: true });
	}
	return {
		signal: controller.signal,
		done: () => {
			clearTimeout(timer);
			signal?.removeEventListener("abort", forward);
		},
	};
}

const VISION_PARAMS = Type.Object({
	image_url: Type.String({
		description: "Local file path to the image (png, jpg, gif, webp).",
	}),
	question: Type.String({
		description:
			"Your specific question about the image. Be concrete — this is what makes the answer better than a generic description.",
	}),
});

const SEARCH_PARAMS = Type.Object({
	query: Type.String({ minLength: 2, description: "The search query to use" }),
	allowed_domains: Type.Optional(
		Type.Array(Type.String(), { description: "Only include search results from these domains" }),
	),
	blocked_domains: Type.Optional(
		Type.Array(Type.String(), { description: "Never include search results from these domains" }),
	),
});

async function analyzeImage(
	imagePath: string,
	question: string,
	signal: AbortSignal | undefined,
): Promise<string> {
	const apiKey = (process.env.PI_VISION_API_KEY || process.env.OPENAI_API_KEY || "").trim();
	if (!apiKey) {
		throw new Error(
			"vision_analyze is not configured: set OPENAI_API_KEY (or PI_VISION_API_KEY) to a key that can reach a vision-capable model.",
		);
	}
	const model = visionModel();
	const baseUrl = (process.env.PI_VISION_BASE_URL || "https://api.openai.com/v1").trim().replace(/\/+$/, "");

	const absolute = resolve(imagePath);
	const extension = extname(absolute).toLowerCase();
	const mimeType = IMAGE_MIME_TYPES[extension];
	if (!mimeType) {
		throw new Error(
			`Unsupported image extension ${extension || "(none)"} for ${absolute}. Supported: ${Object.keys(IMAGE_MIME_TYPES).join(", ")}.`,
		);
	}

	let bytes: Buffer;
	try {
		bytes = await readFile(absolute);
	} catch (error) {
		throw new Error(`Could not read image ${absolute}: ${(error as Error).message}`);
	}
	if (bytes.byteLength > MAX_IMAGE_BYTES) {
		throw new Error(
			`Image ${absolute} is ${bytes.byteLength} bytes, over the ${MAX_IMAGE_BYTES}-byte limit. Downscale it first (e.g. with ImageMagick) and retry.`,
		);
	}

	const dataUri = `data:${mimeType};base64,${bytes.toString("base64")}`;
	const timeout = withTimeout(signal, VISION_TIMEOUT_MS);
	try {
		const response = await fetch(`${baseUrl}/chat/completions`, {
			method: "POST",
			headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
			body: JSON.stringify({
				model,
				messages: [
					{
						role: "user",
						content: [
							{ type: "text", text: question },
							{ type: "image_url", image_url: { url: dataUri } },
						],
					},
				],
			}),
			signal: timeout.signal,
		});
		if (!response.ok) {
			const detail = (await response.text()).slice(0, 500);
			throw new Error(`Vision model ${model} returned HTTP ${response.status}: ${detail}`);
		}
		const payload = (await response.json()) as {
			choices?: Array<{ message?: { content?: string } }>;
		};
		const answer = payload.choices?.[0]?.message?.content?.trim();
		if (!answer) throw new Error(`Vision model ${model} returned an empty answer.`);
		return answer;
	} finally {
		timeout.done();
	}
}

interface SearchHit {
	title: string;
	url: string;
	snippet: string;
}

async function searchWeb(
	query: string,
	allowedDomains: string[] | undefined,
	blockedDomains: string[] | undefined,
	signal: AbortSignal | undefined,
): Promise<SearchHit[]> {
	const apiKey = (process.env.TAVILY_API_KEY || "").trim();
	if (!apiKey) {
		throw new Error(
			"websearch is not configured. Set the TAVILY_API_KEY environment variable (get a free key at https://app.tavily.com).",
		);
	}

	const body: Record<string, unknown> = { query, max_results: 15, include_answer: false };
	if (allowedDomains && allowedDomains.length > 0) body.include_domains = allowedDomains;
	if (blockedDomains && blockedDomains.length > 0) body.exclude_domains = blockedDomains;

	const timeout = withTimeout(signal, SEARCH_TIMEOUT_MS);
	try {
		const response = await fetch("https://api.tavily.com/search", {
			method: "POST",
			headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
			body: JSON.stringify(body),
			signal: timeout.signal,
		});
		if (!response.ok) {
			const detail = (await response.text()).slice(0, 300);
			throw new Error(`Tavily search error ${response.status}: ${detail}`);
		}
		const payload = (await response.json()) as {
			results?: Array<{ title?: string; url?: string; content?: string }>;
		};
		return (payload.results ?? []).map((hit) => ({
			title: (hit.title ?? "").trim(),
			url: (hit.url ?? "").trim(),
			snippet: (hit.content ?? "").trim(),
		}));
	} finally {
		timeout.done();
	}
}

export default function terminalBenchToolsExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "vision_analyze",
		label: "Vision",
		description:
			"Ask a vision-capable model about a local image and get a text answer. Use this when you need to see an image but your own model cannot, or when an image was described to you and you need a closer look at something specific. Always pass a concrete question — 'what is the total amount and VAT on this invoice?' beats 'describe this'.",
		promptSnippet: "ask a vision model a concrete question about a local image file",
		parameters: VISION_PARAMS,
		async execute(_toolCallId, params, signal) {
			const answer = await analyzeImage(params.image_url, params.question, signal);
			return {
				content: [{ type: "text", text: truncate(answer) }],
				details: { image: params.image_url, model: visionModel() },
			};
		},
	});

	pi.registerTool({
		name: "websearch",
		label: "Web search",
		description:
			"Search the web and return the top results as title/url/snippet. Use it for facts that are outside the local machine or newer than your training data. Follow up with a fetch (e.g. curl via bash) when a result needs reading in full.",
		promptSnippet: "search the web for information not available locally",
		parameters: SEARCH_PARAMS,
		async execute(_toolCallId, params, signal) {
			const hits = await searchWeb(params.query, params.allowed_domains, params.blocked_domains, signal);
			if (hits.length === 0) {
				return {
					content: [{ type: "text", text: `No results for: ${params.query}` }],
					details: { query: params.query, results: 0 },
				};
			}
			const rendered = hits
				.map((hit, index) => `${index + 1}. ${hit.title}\n   ${hit.url}\n   ${hit.snippet}`)
				.join("\n\n");
			return {
				content: [{ type: "text", text: truncate(`Search results for: ${params.query}\n\n${rendered}`) }],
				details: { query: params.query, results: hits.length },
			};
		},
	});
}
