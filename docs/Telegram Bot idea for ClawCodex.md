Telegram-related information

Supported capabilities

Capability	Telegram
Voice	✅
Images	✅
Files	✅
Threads	✅
Reactions	—
Typing indicator	✅
Streaming responses	✅

Voice support includes TTS audio replies and/or transcription of voice messages.



Setup

Use the interactive setup wizard:

hermes gateway setup

Select Telegram in the wizard and provide its required configuration.

The documentation also links to a dedicated Telegram Setup page.

Access control

Allow specific Telegram users

TELEGRAM_ALLOWED_USERS=123456789,987654321

Or use the general gateway allowlist:

GATEWAY_ALLOWED_USERS=123456789,987654321

Allowing every user is possible but not recommended:

GATEWAY_ALLOW_ALL_USERS=true

DM pairing alternative

Unknown Telegram users can receive a temporary pairing code in a direct message. Approve it with:

hermes pairing approve telegram XKGH5N7P

Other pairing commands:

hermes pairing list
hermes pairing revoke telegram 123456789

Pairing codes expire after one hour.


Telegram tool access

Telegram uses the hermes-telegram toolset, which includes full tools, including terminal access.


Telegram-specific configuration examples

Home chat and restart notification

gateway:
  platforms:
    telegram:
      home_chat_id: "123456789"
      gateway_restart_notification: false

gateway_restart_notification defaults to true. Set it to false to suppress messages sent to the home chat after gateway restarts or interrupted sessions.

Typing indicator

Telegram shows a typing indicator while Hermes processes messages by default.

gateway:
  platforms:
    telegram:
      typing_indicator: false

Set it to false to disable the indicator.

Per-channel model or prompt overrides

Telegram is included in the general per-platform channel override system. The available override fields are:

model: provider/model-name
provider: provider-name
system_prompt: "Custom instructions for this chat."

A session-level /model choice takes priority over a channel override.


Telegram mobile-friendly defaults

Telegram defaults are designed to keep mobile chats cleaner:

- Tool-progress message streams are not shown by default.
- Busy acknowledgments are brief.
- Real assistant mid-response messages remain enabled.
- Long tasks show one editable “working” status message with periodic updates.

To change these settings:

display:
  platforms:
    telegram:
      tool_progress: new
      busy_ack_detail: true
      interim_assistant_messages: false
      long_running_notifications: false


Progress-message cleanup

Telegram supports automatic deletion of tool-progress and working-status messages after a successful final answer:

display:
  platforms:
    telegram:
      cleanup_progress: true

This is disabled by default. Failed runs keep progress messages visible.


Platform reset policy override

Set Telegram-specific session reset behavior in ~/.hermes/gateway.json:

{
  "reset_by_platform": {
    "telegram": {
      "mode": "idle",
      "idle_minutes": 240
    }
  }
}


Managing Telegram while the gateway is running

Use /platform from a connected chat or CLI session:

/platform list
/platform pause telegram
/platform resume telegram

If repeated retryable Telegram failures occur, Hermes can pause its Telegram adapter through a circuit breaker. Check:

/platform list
~/.hermes/logs/gateway.log
Telegram’s service-status information

Resume Telegram manually after the issue is resolved:

/platform resume telegram


Tool progress controls

Control tool-progress output globally:

display:
  tool_progress: log
  tool_progress_command: false
  tool_progress_grouping: accumulate

Options:

Setting	Meaning
tool_progress: false	No tool-progress messages.
tool_progress: new	Show tool-progress updates in chat.
tool_progress: verbose	Show more detailed progress.
tool_progress: log	Write tool activity to an audit log rather than Telegram.
tool_progress_grouping: accumulate	Edit or update one progress message where supported.
tool_progress_grouping: separate	Send separate progress messages per tool.

With log mode, tool calls are written to:

~/.clawcodex/logs/tool_calls.log

The log rotates at 5 MB with 3 backups and uses secret redaction.


Custom Telegram status phrases

Customize long-running Telegram status messages, such as “Still working…”:

display:
  status_phrases:
    path: status_phrases/telegram.yaml
    mode: append

A status phrase file can look like:

status:
  - "Checking that now…"
  - "Still working on it…"
  - "One moment while I finish this…"

generic:
  - "Processing your request…"

Limits:

- Up to 80 phrases per message category
- Maximum 160 characters per phrase
- Tool arguments, reasoning, and raw commands are not inserted into these phrases


Linux watchdog option

For a Linux systemd-managed Telegram gateway, configure an event-loop watchdog:

gateway:
  systemd_watchdog_seconds: 120

Then regenerate the service unit:

clawcodex gateway install --force

A positive setting configures systemd to restart the gateway if its event loop stops making timely progress. This is intended for application stalls, not ordinary Telegram network disconnects.


Telegram-related operational notes

- Telegram supports image and file attachments in both directions.
- Telegram supports threaded conversations.
- Telegram supports streaming responses and typing indicators.
- Telegram does not provide Hermes-style reaction support in the listed capability matrix.
- Telegram is suitable for mobile use because its default progress behavior minimizes excess messages.
- Keep Telegram access restricted unless you specifically intend to operate a publicly accessible bot.


Telegram Messaging Gateway — Clawcodex

Clawcodex can connect to Telegram through its messaging gateway. The gateway runs as a single background process that receives Telegram messages, keeps conversation sessions, runs scheduled jobs, sends replies, handles voice messages, and manages delivery recovery.

Telegram capability summary

Capability	Supported
Voice messages / TTS replies	✅
Image sending and receiving	✅
File attachments	✅
Threaded conversations	✅
Emoji reactions	—
Typing indicator	✅
Streaming reply updates	✅

Voice includes text-to-speech audio replies and/or transcription of received voice messages.


Quick setup

Run the interactive configuration wizard:

clawcodex gateway setup

The wizard lets you choose Telegram, enter the required Telegram bot configuration, review existing platform settings, and start or restart the gateway afterward.

Run the gateway manually in the foreground:

clawcodex gateway


Gateway service commands

clawcodex gateway install
clawcodex gateway start
clawcodex gateway stop
clawcodex gateway status


Linux system service

sudo clawcodex gateway install --system
sudo clawcodex gateway start --system
sudo clawcodex gateway status --system


Linux user-service logs

journalctl --user -u clawcodex-gateway -f


Linux system-service logs

journalctl -u clawcodex-gateway -f


macOS logs

tail -f ~/.clawcodex/logs/gateway.log


Telegram access control

Telegram bots should be restricted to approved users because the Telegram integration can access the full Clawcodex toolset, including terminal tools.

Allow specific Telegram users

Set Telegram numeric user IDs in an environment variable:

TELEGRAM_ALLOWED_USERS=123456789,987654321

You may alternatively set the general gateway allowlist:

GATEWAY_ALLOWED_USERS=123456789,987654321

Allowing all users is available but unsafe when the bot has access to system tools:

GATEWAY_ALLOW_ALL_USERS=true


Telegram DM pairing

Instead of preconfiguring user IDs, unknown Telegram users may receive a one-time pairing code by direct message.

The user receives a message similar to:

Pairing code: XKGH5N7P

Approve the pairing request:

clawcodex pairing approve telegram XKGH5N7P

List pairing requests and approved users:

clawcodex pairing list

Remove an approved Telegram user:

clawcodex pairing revoke telegram 123456789

Pairing codes:

- Expire after one hour
- Are rate-limited
- Use cryptographically secure randomness


Telegram conversations and session behavior

Telegram conversations remain persistent across messages. The agent retains prior context until you reset or start a new session.

In-chat commands

Command	Purpose
/new or /reset	Start a fresh conversation
/model [provider:model]	Show or change the model
/personality [name]	Select a personality; use none to reset
/retry	Retry the previous response
/undo	Remove the latest exchange
/status	Show current session information
/whoami	Show your access tier and allowed commands
/stop	Stop active work
/approve	Approve a pending dangerous action
/deny	Reject a pending dangerous action
/sethome	Set the current Telegram chat as the home channel
/compress	Compress conversation context
/title [name]	Set or display the session title
/resume [name]	Resume a named session
/sessions	List sessions for the current chat
/sessions search 	Find sessions by title or session ID
/usage	Show session token usage
/usage reset --force	Redeem/reset supported banked usage limits
/insights [days]	Show usage analytics
/reasoning [level|show|hide]	Change reasoning settings or visibility
/voice [on|off|tts|status]	Control Telegram voice/TTS behavior
/rollback [number]	List or restore filesystem checkpoints
/background 	Run a separate background task
/reload-mcp	Reload MCP server configuration
/update	Update Clawcodex
/help	Show available commands
/	Run an installed skill

Model selection persistence

A model chosen through Telegram persists for that session across gateway restarts:

/model openai:gpt-5-mini

Useful options:

/model anthropic:claude-sonnet-4.6
/model openai:gpt-5-mini --once
/model openai:gpt-5-mini --global

- Standard /model applies to the current session.
- --once applies only to the next turn.
- --global writes the choice to global configuration.
- /new and /remove the session-level model override.

Credentials are resolved when needed and are not saved in session records.


Telegram user permissions

Telegram users can be divided into admins and regular users.

- Admins can run all registered commands, including plugin commands and gated capabilities.
- Regular users can chat normally but may be limited to selected slash commands.
- /help and /whoami are always available.

Example configuration:

gateway:
  platforms:
    telegram:
      extra:
        allow_from:
          - "111111111"
          - "222222222"
          - "333333333"
        allow_admin_from:
          - "111111111"
        user_allowed_commands:
          - status
          - model

Use /whoami in Telegram to view the active access scope, tier, and available commands.

If allow_admin_from is not set, the admin/regular-user split is disabled and permitted users retain unrestricted command access for compatibility.


Telegram message handling

Typing indicator

Telegram shows a typing indicator while Clawcodex is processing a request by default.

Disable it if desired:

gateway:
  platforms:
    telegram:
      typing_indicator: false

This changes only the typing indicator; message processing and replies continue normally.


Streaming and mid-task updates

Telegram supports progressive response updates. It also supports assistant status messages during long-running work.

Telegram’s default mobile-focused behavior is intended to reduce chat noise:

- Per-tool progress updates are generally not shown by default.
- Busy acknowledgments are brief.
- Real assistant in-progress messages remain visible.
- Long-running tasks use a single edited status message such as “Working — 3 min.”
- Final messages are sent after processing completes.

Change Telegram display behavior:

display:
  platforms:
    telegram:
      tool_progress: new
      busy_ack_detail: true
      interim_assistant_messages: false
      long_running_notifications: false


Auto-delete progress messages

Telegram can remove temporary tool-progress and working-status messages when a task succeeds:

display:
  platforms:
    telegram:
      cleanup_progress: true

Notes:

- Disabled by default.
- Available on Telegram.
- Failed tasks keep progress messages as useful history.


Busy-message behavior

When a new Telegram message arrives while the agent is working, the default mode is to redirect or interrupt the active turn as appropriate.

Available modes:

display:
  busy_input_mode: interrupt
  busy_ack_enabled: true

Valid values:

display:
  busy_input_mode: queue
  busy_input_mode: steer
  busy_input_mode: interrupt

Behavior:

Mode	Result
interrupt	New Telegram input restarts or redirects active generation.
queue	The new message waits until current work completes.
steer	The message is fed into the active task at the next safe tool-result boundary.

Disable visible busy acknowledgments:

display:
  busy_ack_enabled: false


Intentional silence

Clawcodex can intentionally produce no Telegram reply when its full final output is exactly one of these tokens:

[SILENT]
SILENT
NO_REPLY
NO REPLY

Rules:

- Matching ignores capitalization and surrounding whitespace.
- The entire final response must be exactly one supported token.
- A sentence containing a token is sent normally.
- The silent turn remains in the internal conversation history.
- Failed requests still show errors instead of being hidden.

Example internal history:

user: side-channel chatter
assistant: [SILENT]
user: next message

The [SILENT] answer is retained internally but is not delivered to Telegram.


Telegram voice support

Telegram supports voice-related features, including:

- Receiving voice messages
- Voice-message transcription
- Text-to-speech audio replies
- Voice reply controls through /voice

Examples:

/voice on
/voice off
/voice tts
/voice status


Background tasks from Telegram

Run an independent task without blocking the current Telegram conversation:

/background Check server health and report any failures

Example confirmation:

🔄 Background task started: "Check server health and report any failures"
Task ID: bg_143022_a1b2c3

When complete, Clawcodex sends the result to the originating Telegram chat:

✅ Background task complete

If the task fails:

❌ Background task failed

Background tasks:

- Use a separate, isolated session.
- Do not receive the main Telegram chat’s conversation history.
- Inherit the active model, tool configuration, provider settings, and reasoning configuration.
- Let you continue chatting while work runs.


Background process notifications

If a Telegram-initiated task starts a background process such as a server, build, or long-running command, configure process notifications with:

display:
  background_process_notifications: concise

Available settings:

Value	Telegram behavior
concise	One-line completion update; failures include a short output tail.
all	Running updates plus final raw output.
result	Final raw output regardless of success or failure.
error	Final raw output only for non-zero exit status.
off	No background-process notifications.

Or use an environment variable:

CLAWCODEX_BACKGROUND_NOTIFICATIONS=result


Telegram session reset policies

By default, sessions do not reset automatically. Use /reset when you need a fresh context.

Global reset configuration:

session_reset:
  mode: idle
  idle_minutes: 1440
  at_hour: 4

Supported modes:

Mode	Behavior
none	Never reset automatically; default.
daily	Reset once daily at at_hour.
idle	Reset after inactivity for idle_minutes.
both	Reset when either daily or idle rule triggers first.

Telegram-specific override in ~/.clawcodex/gateway.json:

{
  "reset_by_platform": {
    "telegram": {
      "mode": "idle",
      "idle_minutes": 240
    }
  }
}

A live background process normally prevents session reset while it is running. The maximum age for such reset protection defaults to 24 hours.

bg_process_max_age_hours: 24

Set it to 0 to keep the prior behavior where any live background process prevents a reset indefinitely.


Telegram channel-specific model and prompt settings

Different Telegram chats can use different models or instructions.

Example in ~/.clawcodex/gateway-config.yaml:

platforms:
  telegram:
    enabled: true
    channel_overrides:
      "123456789":
        model: openai/gpt-5-mini
      "-1001234567890":
        model: anthropic/claude-sonnet-4.6
        provider: anthropic
        system_prompt: "You are the code-review assistant for this Telegram group."

Each override can contain:

model: provider/model-name
provider: provider-name
system_prompt: "Instructions for this Telegram chat."

Priority order for model selection:

1. Session-level /model override
2. Telegram channel_overrides
3. Global configured default model

The custom system_prompt is applied for the current turn and is not written into the chat transcript.


Telegram timestamps in model context

To provide the agent with Telegram message times, enable timestamps:

gateway:
  message_timestamps:
    enabled: true

When enabled, the model receives a prefix similar to:

[Tue 2026-04-28 13:40:53 CEST]

This can help it recognize long gaps between messages or answer time-based questions. Timestamps are not inserted into assistant messages or permanently duplicated in transcripts.


Telegram delivery reliability

Final Telegram replies are stored in a durable delivery ledger before and around delivery.

If Clawcodex crashes after creating a response but before Telegram confirms delivery, it attempts to send the saved response after restart.

Behavior:

- Replies never started are re-sent normally.
- Replies interrupted during sending may be re-sent with a recovery prefix:

♻️ Recovered reply — …

This indicates the Telegram message may be duplicated.

Limits:

- Up to 3 redelivery attempts
- Up to 24 hours of freshness
- Successfully delivered records are cleaned up after 7 days

Disable this feature:

gateway:
  delivery_ledger: false


Telegram restart and interrupted-session behavior

If the gateway restarts while Telegram work is in progress:

- The affected session is marked as interrupted.
- On the next startup, Clawcodex schedules an automatic resume attempt.
- Telegram may receive a short notice asking the user to send a message so work can resume.
- A gateway restart notification can be sent to the Telegram home chat.

Configure the Telegram home chat and disable restart notices:

gateway:
  platforms:
    telegram:
      home_chat_id: "123456789"
      gateway_restart_notification: false

gateway_restart_notification defaults to true.


Telegram platform management

Use /platform from a connected session to inspect or control Telegram without restarting the full gateway:

/platform list
/platform pause telegram
/platform resume telegram

Command	Action
/platform list	Shows Telegram adapter state and failure details.
/platform pause telegram	Stops processing new Telegram messages while keeping the connection loaded.
/platform resume telegram	Restores Telegram message processing and clears a tripped breaker.


Telegram circuit breaker

Repeated retryable failures can automatically pause the Telegram adapter. Typical triggers include:

- Network failures
- Telegram rate limiting
- Telegram API 5xx errors
- Connection interruptions
- WebSocket-related disconnects, where applicable

When paused:

- Incoming Telegram messages are dropped until Telegram is resumed.
- The gateway logs the reason.
- A notification may be sent to a configured home channel on another live platform.
- Telegram is not automatically resumed, preventing repeated reconnection attempts.

Check status with:

/platform list

Check logs:

tail -f ~/.clawcodex/logs/gateway.log

After Telegram recovers:

/platform resume telegram


Telegram tool progress controls

Control tool-progress output globally:

display:
  tool_progress: log
  tool_progress_command: false
  tool_progress_grouping: accumulate

Options:

Setting	Meaning
tool_progress: false	No tool-progress messages.
tool_progress: new	Show tool-progress updates in chat.
tool_progress: verbose	Show more detailed progress.
tool_progress: log	Write tool activity to an audit log rather than Telegram.
tool_progress_grouping: accumulate	Edit or update one progress message where supported.
tool_progress_grouping: separate	Send separate progress messages per tool.

With log mode, tool calls are written to:

~/.clawcodex/logs/tool_calls.log

The log rotates at 5 MB with 3 backups and uses secret redaction.


Custom Telegram status phrases

Customize long-running Telegram status messages, such as “Still working…”:

display:
  status_phrases:
    path: status_phrases/telegram.yaml
    mode: append

A status phrase file can look like:

status:
  - "Checking that now…"
  - "Still working on it…"
  - "One moment while I finish this…"

generic:
  - "Processing your request…"

Limits:

- Up to 80 phrases per message category
- Maximum 160 characters per phrase
- Tool arguments, reasoning, and raw commands are not inserted into these phrases


Linux watchdog option

For a Linux systemd-managed Telegram gateway, configure an event-loop watchdog:

gateway:
  systemd_watchdog_seconds: 120

Then regenerate the service unit:

clawcodex gateway install --force

A positive setting configures systemd to restart the gateway if its event loop stops making timely progress. This is intended for application stalls, not ordinary Telegram network disconnects.


Telegram-related operational notes

- Telegram supports image and file attachments in both directions.
- Telegram supports threaded conversations.
- Telegram supports streaming responses and typing indicators.
- Telegram does not provide Hermes-style reaction support in the listed capability matrix.
- Telegram is suitable for mobile use because its default progress behavior minimizes excess messages.
- Keep Telegram access restricted unless you specifically intend to operate a publicly accessible bot.


## Security Policies for Telegram Integration

### Core Principles
1. **Least Privilege**: Grant only necessary permissions
2. **Zero Trust**: Verify every request
3. **Auditability**: Log all Telegram-initiated actions
4. **Ephemeral Access**: Prefer temporary pairing over permanent whitelists

### Specific Policies

#### Access Control
- **Never enable** `GATEWAY_ALLOW_ALL_USERS=true` in any environment with terminal/file system access
- **Use explicit whitelisting**: `TELEGRAM_ALLOWED_USERS=<your_user_id>,<trusted_team_ids>`
- **Consider command-level restrictions** via `user_allowed_commands` to limit available slash commands
- **Regular rotation**: Review and update allowed user list monthly

#### Authentication & Authorization
- **Prefer DM pairing system** for temporary access rather than permanent whitelists when possible
- **Implement admin/regular user split**:
  - Admins: Full access to registered commands (use with extreme caution)
  - Regular users: Limited to safe, read-only commands
- **Never share bot tokens** - regenerate immediately if exposed
- **Store tokens securely** - use environment variables or secret management, never in code

#### Operational Security
- **Enable delivery ledger** for message reliability but monitor for duplicate processing
- **Configure session reset policies**: Use idle timeout (e.g., 60 minutes) to prevent stale sessions
- **Background task restrictions**: Consider disabling background tasks that initiate long-running processes unless absolutely necessary
- **Tool progress monitoring**: Enable `tool_progress: log` in production to audit tool usage without cluttering chat
- **Rate limiting awareness**: Be mindful of Telegram API limits when designing command frequency

#### Monitoring & Auditing
- **Enable message timestamps** (`message_timestamps: true`) for forensic analysis
- **Regular log review**: Monitor `~/.clawcodex/logs/gateway.log` for:
  - Unauthorized access attempts
  - Unusual tool usage patterns
  - Failed command executions
- **Set up alerts** for:
  - Terminal tool usage from Telegram
  - File write/delete operations
  - Process spawning commands

### Risk Mitigation Matrix
| Risk Level | Scenario | Mitigation |
|------------|----------|------------|
| Critical | Terminal access via Telegram | Restrict to admin-only, consider disabling entirely for Telegram |
| High | File system modifications | Limit to specific directories, enable read-only mode where possible |
| Medium | Resource-intensive commands | Implement timeout limits, monitor resource usage |
| Low | Information disclosure | Review what information commands return, avoid leaking secrets |

---

## Recommended Command Sets for Different User Tiers

### 👤 Regular User (Read-Only, Safe Operations)
These commands are safe for general use and don't modify system state:
- `/status` - Show system health and resource usage
- `/logs [service] [lines]` - View recent logs (read-only)
- `/metrics` - Show performance metrics
- `/whoami` - Show your access level and allowed commands
- `/help` - Show available commands
- `/model` - View current model (no changes)
- `/sessions` - List your sessions
- `/usage` - Show token usage
- `/voice [status]` - Check voice settings
- `/insights [days]` - View usage analytics
- `/compress` - Compress conversation context (local only)
- `/title [name]` - Set session title (local only)

### 👨‍💻 Developer User (Limited Write Access)
Includes regular user commands plus:
- `/new` or `/reset` - Start fresh conversation
- `/model [provider:model]` - Change model for session
- `/personality [name]` - Select personality
- `/retry` - Retry previous response
- `/undo` - Remove latest exchange
- `/stop` - Stop active work
- `/approve` / `/deny` - Approve/deny pending dangerous actions
- `/sethome` - Set current chat as home channel
- `/background [task]` - Run background task (consider restrictions)
- `/reload-mcp` - Reload MCP server configuration
- `/update` - Update Clawcodex
- `/reasoning [level|show|hide]` - Adjust reasoning settings
- `/voice [on|off|tts]` - Control voice/TTS behavior
- `/rollback [number]` - List/restore filesystem checkpoints (read-only listing)

### ⚙️ Admin/User (Extended Access - USE WITH EXTREME CAUTION)
Includes developer commands plus:
- `/platform list` - View Telegram adapter state
- `/platform pause telegram` / `/platform resume telegram` - Control Telegram adapter
- **Any skill invocation** via `/` or `/skill name` - **RESTRICT CAREFULLY**
- **File system operations** (if enabled) - **HIGH RISK**
- **Terminal commands** (if enabled) - **CRITICAL RISK**
- **Process management** - **HIGH RISK**

### 🔧 Recommended Configuration Examples

#### For Personal Use (Maximum Security)
```yaml
gateway:
  platforms:
    telegram:
      enabled: true
      extra:
        allow_from: ["123456789"]  # Your user ID only
        allow_admin_from: []       # No admins - use regular user only
        user_allowed_commands:     # Only safe, read-only commands
          - status
          - logs
          - metrics
          - whoami
          - help
          - model
          - sessions
          - usage
          - compress
          - title
          - insights
          - voice
  display:
    platforms:
      telegram:
        tool_progress: log         # Log tool usage instead of showing in chat
        cleanup_progress: true     # Auto-delete progress messages
  session_reset:
    mode: idle
    idle_minutes: 30               # Reset after 30 minutes idle
  message_timestamps: true         # Enable timestamps for auditing
```

#### For Team Development (Controlled Access)
```yaml
gateway:
  platforms:
    telegram:
      enabled: true
      extra:
        allow_from: ["111111111","222222222","333333333"]  # Team member IDs
        allow_admin_from: ["111111111"]                     # Team lead only as admin
        user_allowed_commands:                              # Regular team members
          - status
          - logs
          - metrics
          - whoami
          - help
          - model
          - sessions
          - usage
          - voice
          - insights
          - compress
          - title
          - new
          - reset
          - retry
          - undo
          - stop
          - approve
          - deny
      # Admins get additional commands (configure via platform settings)
  display:
    platforms:
      telegram:
        tool_progress: new        # Show progress in chat for development
        busy_ack_detail: true
  session_reset:
    mode: idle
    idle_minutes: 60
  message_timestamps: true
  background_process_notifications: concise
```

### 🚨 Emergency Procedures
1. **Immediate Revocation**: If token compromised, revoke via @BotFather immediately
2. **Gateway Shutdown**: `clawcodex gateway stop` to halt all Telegram processing
3. **Access Review**: Check `clawcodex pairing list` and revoke any suspicious pairings
4. **Log Analysis**: Review logs for unauthorized activity period
5. **Token Rotation**: Generate new bot token and update configuration

### 📋 Implementation Checklist
- [ ] Never commit bot token to version control
- [ ] Use environment variables or secret management for token storage
- [ ] Start with most restrictive command set, gradually expand as needed
- [ ] Test all commands in isolated environment before production use
- [ ] Document approved use cases for each command tier
- [ ] Schedule monthly security review of Telegram integration
- [ ] Train all users on security policies and proper usage
- [ ] Establish incident response plan for Telegram-specific breaches

## 🔐 Final Security Reminder
The Telegram-Clawcodex integration provides powerful remote access to your development environment. Treat it with the same rigor as SSH keys or production database credentials. The convenience of Telegram messaging should never outweigh security considerations. Regular audits, least-privilege access, and vigilant monitoring are essential for safe operation.