<div align="center" dir="rtl">

[English](../../README.md) | [中文](README_ZH.md) | [Français](README_FR.md) | [Русский](README_RU.md) | [हिन्दी](README_HI.md) | **العربية** | [Português](README_PT.md)

# 🚀 Claude Code Python

**إعادة تنفيذ كاملة بلغة Python استنادًا إلى كود Claude Code الأصلي**

*من كود TypeScript → أعيد بناؤه بـ Python بـ ❤️*

***

[![GitHub stars](https://img.shields.io/github/stars/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=yellow)](https://github.com/GPT-AGI/Claw-Codex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=blue)](https://github.com/GPT-AGI/Claw-Codex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)

**🔥 تطوير نشط • ميزات جديدة أسبوعيًا 🔥**

</div>

***

## 🎯 ما هذا؟

**Claw Codex** هو **إعادة كتابة كاملة بلغة Python** لـ Claude Code، استنادًا إلى **كود TypeScript الحقيقي**.

### ⚠️ مهم: هذا ليس مجرد كود مصدر

**على عكس كود TypeScript المُسرّب**، Claw Codex هو **أداة CLI تعمل بالكامل**:

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

**CLI حقيقي • استخدام حقيقي • مجتمع حقيقي**

</div>

- ✅ **CLI يعمل** — ليس مجرد كود، بل أداة سطر أوامر تعمل بالكامل يمكنك استخدامها اليوم
- ✅ **استنادًا إلى المصدر الحقيقي** — تم نقله من تنفيذ Claude Code TypeScript الفعلي
- ✅ **أقصى درجات الدقة** — يحافظ على البنية الأصلية مع التحسين
- ✅ **Python أصلي** — كود Python نظيف ومعبر مع تعليقات نوع كاملة
- ✅ **سهل الاستخدام** — إعداد سهل، REPL تفاعلي، توثيق شامل
- ✅ **تحسين مستمر** — معالجة أخطاء محسّنة، اختبارات، توثيق

**🚀 جرّبه الآن! افرکه، عدّله، اجعله ملكك! طلبات السحب مرحب بها!**

***

## ⭐ Star History

<a href="https://www.star-history.com/?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
 </picture>
</a>

## ✨ الميزات

### دعم متعدد المزودين

```python
providers = ["Anthropic Claude", "OpenAI GPT", "Zhipu GLM"]  # + سهل التوسيع
```

### REPL تفاعلي

```text
>>> مرحبًا!
Assistant: أهلاً! أنا Claw Codex، إعادة تنفيذ بـ Python...

>>> /help         # عرض الأوامر
>>> /             # عرض الأوامر والـ skills
>>> /save         # حفظ الجلسة
>>> /multiline    # وضع متعدد الأسطر
>>> Tab           # الإكمال التلقائي
>>> /explain-code qsort.py   # تشغيل skill
```

### Skills (Slash Commands)

See [README.md](../../README.md#skills-slash-commands) for a quick tutorial on creating skills under `.clawcodex/skills/<skill-name>/SKILL.md`.

### CLI كامل

```bash
clawcodex              # بدء REPL
clawcodex login        # تكوين API
clawcodex --version    # التحقق من الإصدار
clawcodex config       # عرض الإعدادات
```

***

## 📊 الحالة

| المكون           | الحالة  | العدد       |
| ---------------- | ------- | ----------- |
| الأوامر          | ✅ مكتمل | 150+        |
| الأدوات          | ✅ مكتمل | 100+        |
| تغطية الاختبارات | ✅ 90%+  | 75+ اختبار  |
| التوثيق          | ✅ مكتمل | 10+ مستندات |

***

## 🚀 البدء السريع

### التثبيت

```bash
git clone https://github.com/GPT-AGI/Claw-Codex.git
cd Claw-Codex

# إنشاء venv (يُوصى بـ uv)
uv venv --python 3.11
source .venv/bin/activate

# التثبيت
uv pip install -r requirements.txt
```

### التكوين

#### الخيار 1: تفاعلي (مُوصى به)

```bash
python -m src.cli login
```

هذه العملية ستقوم بـ:

1. مطالبتك باختيار مزود: anthropic / openai / glm
2. مطالبتك بمفتاح API الخاص بذلك المزود
3. حفظ عنوان URL أساسي مخصص اختياريًا
4. حفظ نموذج افتراضي اختياريًا
5. تعيين المزود المحدد كافتراضي

يتم حفظ ملف التكوين في `~/.clawcodex/config.json`. مثال على الهيكل:

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

### التشغيل

```bash
python -m src.cli          # بدء REPL
python -m src.cli --help   # عرض المساعدة
```

**هذا كل شيء!** ابدأ الدردشة مع AI في 3 خطوات.

***

## 💡 الاستخدام

### أوامر REPL

| الأمر        | الوصف                  |
| ------------ | ---------------------- |
| `/help`      | عرض جميع الأوامر       |
| `/save`      | حفظ الجلسة             |
| `/load <id>` | تحميل جلسة             |
| `/multiline` | تبديل وضع متعدد الأسطر |
| `/clear`     | مسح السجل              |
| `/exit`      | الخروج من REPL         |

### مثال على الجلسة

![مثال على الجلسة](../../assets/claw-codex-tool-skill-json.png)

***

## 🎓 لماذا Claw Codex؟

### استنادًا إلى الكود المصدري الحقيقي

- **ليس نسخة** — تم نقله من تنفيذ TypeScript الفعلي
- **دقة هيكلية** — يحافظ على أنماط التصميم المثبتة
- **تحسينات** — معالجة أخطاء أفضل، المزيد من الاختبارات، كود أنظف

### Python أصلي

- **تعليقات النوع** — تعليقات نوع كاملة
- **Python حديث** — يستخدم ميزات 3.10+
- **معبر** — كود Python نظيف

### يركز على المستخدم

- **إعداد من 3 خطوات** — استنساخ، تكوين، تشغيل
- **تكوين تفاعلي** — `clawcodex login` يرشدك
- **REPL غني** — إكمال Tab، تمييز بناء الجملة
- **استمرار الجلسة** — لا تفقد عملك أبدًا

***

## 📦 هيكل المشروع

```text
Claw-Codex/
├── src/
│   ├── cli.py           # مدخل CLI
│   ├── config.py        # التكوين
│   ├── repl/            # REPL تفاعلي
│   ├── providers/       # مزودو LLM
│   └── agent/           # إدارة الجلسات
├── tests/               # 75+ اختبار
└── docs/                # توثيق كامل
```

***

## 🗺️ خارطة الطريق

- [x] Python MVP
- [x] دعم متعدد المزودين
- [x] استمرار الجلسة
- [x] تدقيق الأمان
- [ ] نظام استدعاء الأدوات
- [ ] حزمة PyPI
- [ ] إصدار Go

***

## 🤝 المساهمة

**نرحب بالمساهمات!**

```bash
# إعداد تطوير سريع
pip install -e .[dev]
python -m pytest tests/ -v
```

راجع [CONTRIBUTING.md](../../CONTRIBUTING.md) للإرشادات.

***

## 📖 التوثيق

- **[SETUP_GUIDE.md](../guide/SETUP_GUIDE.md)** — التثبيت المفصل
- **[CONTRIBUTING.md](../../CONTRIBUTING.md)** — دليل التطوير
- **[TESTING.md](../guide/TESTING.md)** — دليل الاختبار
- **[CHANGELOG.md](../../CHANGELOG.md)** — تاريخ الإصدارات

***

## ⚡ الأداء

- **بدء التشغيل**: < 1 ثانية
- **الذاكرة**: < 50MB
- **الاستجابة**: دفق (في الوقت الحقيقي)

***

## 🔒 الأمان

✅ **تم تدقيق الأمان**

- لا بيانات حساسة في Git
- مفاتيح API مشفرة في التكوين
- ملفات `.env` تم تجاهلها
- آمن للإنتاج

***

## 📄 الترخيص

ترخيص MIT — راجع [LICENSE](../../LICENSE)

***

## 🙏 الشكر

- استنادًا إلى كود Claude Code TypeScript
- مشروع تعليمي مستقل
- غير تابع لـ Anthropic

***

<div align="center">

### 🌟 أظهر دعمك

إذا وجدت هذا مفيدًا، يرجى **star** ⭐ للمستودع!

**صُنع بـ ❤️ بواسطة فريق Claw Codex**

[⬆ العودة للأعلى](#-claw-codex)

</div>
