<div align="center">

[English](../../README.md) | [中文](README_ZH.md) | [Français](README_FR.md) | [Русский](README_RU.md) | **हिन्दी** | [العربية](README_AR.md) | [Português](README_PT.md)

# 🚀 Claude Code Python

**वास्तविक Claude Code स्रोत कोड पर आधारित एक पूर्ण Python पुनर्कार्यान्वयन**

*TypeScript स्रोत से → Python में ❤️ के साथ पुनर्निर्मित*

***

[![GitHub stars](https://img.shields.io/github/stars/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=yellow)](https://github.com/GPT-AGI/Claw-Codex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=blue)](https://github.com/GPT-AGI/Claw-Codex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)

**🔥 सक्रिय विकास • साप्ताहिक नई सुविधाएँ 🔥**

</div>

***

## 🎯 यह क्या है?

**Claw Codex** Claude Code का एक **पूर्ण Python पुनर्लेखन** है, **वास्तविक TypeScript स्रोत कोड** पर आधारित।

### ⚠️ महत्वपूर्ण: यह केवल स्रोत कोड नहीं है

**लीक हुए TypeScript स्रोत के विपरीत**, Claw Codex एक **पूर्ण रूप से कार्यात्मक, चलने योग्य CLI उपकरण** है:

<div align="center">

| **Core Features Showcase** |
|:---:|
| ![Bash Execution](../../assets/claw-codex-bash.png) |
| *Real-time Tool Execution* |
| ![Web Fetch](../../assets/claude-code-webfetch.png) |
| *Instant Web Content Extraction* |
| ![File Operations](../../assets/claw-codex-write-read.png) |
| *Seamless Coding & Debugging* |
| ![Skills (Slash Commands)](../../assets/claw-codex-skill.png) |
| *Flexible Skill Systems* |

**वास्तविक CLI • वास्तविक उपयोग • वास्तविक समुदाय**

</div>

- ✅ **कार्यशील CLI** — केवल कोड नहीं, बल्कि एक पूर्ण रूप से कार्यात्मक कमांड-लाइन उपकरण जिसे आप आज उपयोग कर सकते हैं
- ✅ **वास्तविक स्रोत पर आधारित** — वास्तविक Claude Code TypeScript कार्यान्वयन से पोर्ट किया गया
- ✅ **अधिकतम निष्ठा** — अनुकूलन करते समय मूल आर्किटेक्चर संरक्षित रखता है
- ✅ **Python नेटिव** — स्वच्छ, अभिव्यंजक Python पूर्ण प्रकार संकेतों के साथ
- ✅ **उपयोगकर्ता अनुकूल** — आसान सेटअप, इंटरैक्टिव REPL, व्यापक दस्तावेज़
- ✅ **निरंतर सुधार** — उन्नत त्रुटि हैंडलिंग, परीक्षण, दस्तावेज़ीकरण

**🚀 अभी आज़माएं! इसे फोर्क करें, संशोधित करें, अपना बनाएं! Pull requests का स्वागत है!**

***

## ⭐ Star History

<a href="https://www.star-history.com/?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
 </picture>
</a>

## ✨ विशेषताएँ

### बहु-प्रदाता समर्थन

```python
providers = ["Anthropic Claude", "OpenAI GPT", "Zhipu GLM"]  # + आसानी से विस्तारणीय
```

### इंटरैक्टिव REPL

```text
>>> नमस्ते!
Assistant: नमस्ते! मैं Claw Codex हूं, एक Python पुनर्कार्यान्वयन...

>>> /help         # कमांड दिखाएं
>>> /             # कमांड और skills दिखाएं
>>> /save         # सत्र सहेजें
>>> /multiline    # बहु-पंक्ति मोड
>>> Tab           # स्वत:-पूर्णता
>>> /explain-code qsort.py   # skill चलाएं
```

### Skills (Slash Commands)

See [README.md](../../README.md#skills-slash-commands) for a quick tutorial on creating skills under `.clawcodex/skills/<skill-name>/SKILL.md`.

### पूर्ण CLI

```bash
clawcodex              # REPL प्रारंभ करें
clawcodex login        # API कॉन्फ़िगर करें
clawcodex --version    # संस्करण जांचें
clawcodex config       # सेटिंग्स देखें
```

***

## 📊 स्थिति

| घटक           | स्थिति  | संख्या        |
| ------------- | ------- | ------------- |
| कमांड         | ✅ पूर्ण | 150+          |
| उपकरण         | ✅ पूर्ण | 100+          |
| परीक्षण कवरेज | ✅ 90%+  | 75+ परीक्षण   |
| दस्तावेज़ीकरण | ✅ पूर्ण | 10+ दस्तावेज़ |

***

## 🚀 त्वरित आरंभ

### इंस्टॉल करें

```bash
git clone https://github.com/GPT-AGI/Claw-Codex.git
cd Claw-Codex

# venv बनाएं (uv अनुशंसित)
uv venv --python 3.11
source .venv/bin/activate

# इंस्टॉल करें
uv pip install -r requirements.txt
```

### कॉन्फ़िगर करें

#### विकल्प 1: इंटरैक्टिव (अनुशंसित)

```bash
python -m src.cli login
```

यह प्रक्रिया:

1. आपको एक प्रदाता चुनने के लिए कहेगी: anthropic / openai / glm
2. उस प्रदाता की API कुंजी मांगेगी
3. वैकल्पिक रूप से एक कस्टम base URL सहेजेगी
4. वैकल्पिक रूप से एक डिफ़ॉल्ट मॉडल सहेजेगी
5. चयनित प्रदाता को डिफ़ॉल्ट के रूप में सेट करेगी

कॉन्फ़िगरेशन फ़ाइल `~/.clawcodex/config.json` में सहेजी जाती है। उदाहरण संरचना:

```json
{
  "default_provider": "glm",
  "providers": {
    "anthropic": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.anthropic.com",
      "default_model": "claude-sonnet-4-20250514"
    },
    "openai": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.openai.com/v1",
      "default_model": "gpt-4"
    },
    "glm": {
      "api_key": "base64-encoded-key",
      "base_url": "https://open.bigmodel.cn/api/paas/v4",
      "default_model": "glm-4.5"
    }
  }
}
```

### चलाएं

```bash
python -m src.cli          # REPL प्रारंभ करें
python -m src.cli --help   # सहायता दिखाएं
```

**बस इतना ही!** 3 चरणों में AI के साथ चैट करना शुरू करें।

***

## 💡 उपयोग

### REPL कमांड

| कमांड        | विवरण                    |
| ------------ | ------------------------ |
| `/help`      | सभी कमांड दिखाएं         |
| `/save`      | सत्र सहेजें              |
| `/load <id>` | सत्र लोड करें            |
| `/multiline` | बहु-पंक्ति मोड टॉगल करें |
| `/clear`     | इतिहास साफ़ करें         |
| `/exit`      | REPL से बाहर निकलें      |

### उदाहरण सत्र

![उदाहरण सत्र](../../assets/claw-codex-tool-skill-json.png)

***

## 🎓 Claw Codex क्यों?

### वास्तविक स्रोत कोड पर आधारित

- **क्लोन नहीं** — वास्तविक TypeScript कार्यान्वयन से पोर्ट किया गया
- **आर्किटेक्चरल निष्ठा** — सिद्ध डिज़ाइन पैटर्न बनाए रखता है
- **सुधार** — बेहतर त्रुटि हैंडलिंग, अधिक परीक्षण, क्लीनर कोड

### Python नेटिव

- **प्रकार संकेत** — पूर्ण प्रकार एनोटेशन
- **आधुनिक Python** — 3.10+ सुविधाओं का उपयोग करता है
- **अभिव्यंजक** — स्वच्छ Python कोड

### उपयोगकर्ता केंद्रित

- **3-चरण सेटअप** — क्लोन, कॉन्फ़िगर, चलाएं
- **इंटरैक्टिव कॉन्फ़िगरेशन** — `clawcodex login` आपका मार्गदर्शन करता है
- **समृद्ध REPL** — टैब पूर्णता, सिंटैक्स हाइलाइटिंग
- **सत्र दृढ़ता** — अपना काम कभी न खोएं

***

## 📦 परियोजना संरचना

```text
Claw-Codex/
├── src/
│   ├── cli.py           # CLI प्रविष्टि
│   ├── config.py        # कॉन्फ़िगरेशन
│   ├── repl/            # इंटरैक्टिव REPL
│   ├── providers/       # LLM प्रदाता
│   └── agent/           # सत्र प्रबंधन
├── tests/               # 75+ परीक्षण
└── docs/                # पूर्ण दस्तावेज़
```

***

## 🗺️ रोडमैप

- [x] Python MVP
- [x] बहु-प्रदाता समर्थन
- [x] सत्र दृढ़ता
- [x] सुरक्षा ऑडिट
- [ ] टूल कॉलिंग सिस्टम
- [ ] PyPI पैकेज
- [ ] Go संस्करण

***

## 🤝 योगदान

**हम योगदान का स्वागत करते हैं!**

```bash
# त्वरित देव सेटअप
pip install -e .[dev]
python -m pytest tests/ -v
```

दिशानिर्देशों के लिए [CONTRIBUTING.md](../../CONTRIBUTING.md) देखें।

***

## 📖 दस्तावेज़ीकरण

- **[SETUP_GUIDE.md](../guide/SETUP_GUIDE.md)** — विस्तृत स्थापना
- **[CONTRIBUTING.md](../../CONTRIBUTING.md)** — विकास मार्गदर्शिका
- **[TESTING.md](../guide/TESTING.md)** — परीक्षण मार्गदर्शिका
- **[CHANGELOG.md](../../CHANGELOG.md)** — संस्करण इतिहास

***

## ⚡ प्रदर्शन

- **स्टार्टअप**: < 1 सेकंड
- **मेमोरी**: < 50MB
- **प्रतिक्रिया**: स्ट्रीमिंग (वास्तविक समय)

***

## 🔒 सुरक्षा

✅ **सुरक्षा ऑडिट पूर्ण**

- Git में कोई संवेदनशील डेटा नहीं
- API कुंजी कॉन्फ़िगरेशन में एन्क्रिप्टेड
- `.env` फ़ाइलें अनदेखी की गईं
- उत्पादन के लिए सुरक्षित

***

## 📄 लाइसेंस

MIT लाइसेंस — [LICENSE](../../LICENSE) देखें

***

## 🙏 स्वीकृतियाँ

- Claude Code TypeScript स्रोत पर आधारित
- स्वतंत्र शैक्षिक परियोजना
- Anthropic से संबद्ध नहीं

***

<div align="center">

### 🌟 अपना समर्थन दिखाएं

यदि आपको यह उपयोगी लगता है, तो कृपया **star** ⭐ दें!

**Claw Codex टीम द्वारा ❤️ से बनाया गया**

[⬆ शीर्ष पर वापस](#-claw-codex)

</div>
