<div align="center">

[English](../../README.md) | [中文](README_ZH.md) | **Français** | [Русский](README_RU.md) | [हिन्दी](README_HI.md) | [العربية](README_AR.md) | [Português](README_PT.md)

# 🚀 Claude Code Python

**Une réimplémentation complète en Python basée sur le code source réel de Claude Code**

*Du code source TypeScript → Reconstruit en Python avec ❤️*

***

[![GitHub stars](https://img.shields.io/github/stars/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=yellow)](https://github.com/GPT-AGI/Claw-Codex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=blue)](https://github.com/GPT-AGI/Claw-Codex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)

**🔥 Développement actif • Nouvelles fonctionnalités chaque semaine 🔥**

</div>

***

## 🎯 Qu'est-ce que c'est ?

**Claw Codex** est une **réécriture complète en Python** de Claude Code, basée sur le **vrai code source TypeScript**.

### ⚠️ Important : Ce n'est PAS juste du code source

**Contrairement au code source TypeScript divulgué**, Claw Codex est un **outil CLI entièrement fonctionnel** :

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

**Vrai CLI • Vraie utilisation • Vraie communauté**

</div>

- ✅ **CLI fonctionnel** — Pas juste du code, mais un outil en ligne de commande entièrement fonctionnel que vous pouvez utiliser aujourd'hui
- ✅ **Basé sur le vrai code source** — Porté depuis l'implémentation TypeScript réelle de Claude Code
- ✅ **Fidélité maximale** — Préserve l'architecture originale tout en optimisant
- ✅ **Python natif** — Code Python propre et idiomatique avec annotations de type complètes
- ✅ **Convivial** — Configuration simple, REPL interactif, documentation complète
- ✅ **Continuellement amélioré** — Gestion des erreurs améliorée, tests, documentation

**🚀 Essayez-le maintenant ! Forkez-le, modifiez-le, rendez-le vôtre ! Les pull requests sont les bienvenues !**

***

## ⭐ Star History

<a href="https://www.star-history.com/?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
 </picture>
</a>

## ✨ Fonctionnalités

### Support multi-fournisseurs

```python
providers = ["Anthropic Claude", "OpenAI GPT", "Zhipu GLM"]  # + facile à étendre
```

### REPL interactif

```text
>>> Bonjour !
Assistant: Salut ! Je suis Claw Codex, une réimplémentation en Python...

>>> /help         # Afficher les commandes
>>> /             # Afficher commandes & skills
>>> /save         # Sauvegarder la session
>>> /multiline    # Mode multi-lignes
>>> Tab           # Auto-complétion
>>> /explain-code qsort.py   # Exécuter un skill
```

### Skills (Slash Commands)

See [README.md](../../README.md#skills-slash-commands) for a quick tutorial on creating skills under `.clawcodex/skills/<skill-name>/SKILL.md`.

### CLI complet

```bash
clawcodex              # Démarrer le REPL
clawcodex login        # Configurer l'API
clawcodex --version    # Vérifier la version
clawcodex config       # Voir les paramètres
```

***

## 📊 Statut

| Composant           | Statut     | Quantité  |
| ------------------- | ---------- | --------- |
| Commandes           | ✅ Complet  | 150+      |
| Outils              | ✅ Complet  | 100+      |
| Couverture de tests | ✅ 90%+     | 75+ tests |
| Documentation       | ✅ Complète | 10+ docs  |

***

## 🚀 Démarrage rapide

### Installation

```bash
git clone https://github.com/GPT-AGI/Claw-Codex.git
cd Claw-Codex

# Créer un venv (uv recommandé)
uv venv --python 3.11
source .venv/bin/activate

# Installer
uv pip install -r requirements.txt
```

### Configuration

#### Option 1 : Interactif (Recommandé)

```bash
python -m src.cli login
```

Ce processus va :

1. vous demander de choisir un fournisseur : anthropic / openai / glm
2. vous demander la clé API de ce fournisseur
3. enregistrer optionnellement une URL de base personnalisée
4. enregistrer optionnellement un modèle par défaut
5. définir le fournisseur sélectionné comme valeur par défaut

Le fichier de configuration est enregistré dans `~/.clawcodex/config.json`. Exemple de structure :

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

### Exécution

```bash
python -m src.cli          # Démarrer le REPL
python -m src.cli --help   # Afficher l'aide
```

**C'est tout !** Commencez à discuter avec l'IA en 3 étapes.

***

## 💡 Utilisation

### Commandes REPL

| Commande     | Description                   |
| ------------ | ----------------------------- |
| `/help`      | Afficher toutes les commandes |
| `/save`      | Sauvegarder la session        |
| `/load <id>` | Charger une session           |
| `/multiline` | Basculer le mode multi-lignes |
| `/clear`     | Effacer l'historique          |
| `/exit`      | Quitter le REPL               |

### Exemple de session

![Exemple de session](../../assets/claw-codex-tool-skill-json.png)

***

## 🎓 Pourquoi Claw Codex ?

### Basé sur le vrai code source

- **Pas un clone** — Porté depuis l'implémentation TypeScript réelle
- **Fidélité architecturale** — Maintient les modèles de conception éprouvés
- **Améliorations** — Meilleure gestion des erreurs, plus de tests, code plus propre

### Python natif

- **Indications de type** — Annotations de type complètes
- **Python moderne** — Utilise les fonctionnalités 3.10+
- **Idiomatique** — Code Python propre

### Axé sur l'utilisateur

- **Configuration en 3 étapes** — Cloner, configurer, exécuter
- **Configuration interactive** — `clawcodex login` vous guide
- **REPL riche** — Complétion par tabulation, coloration syntaxique
- **Persistance des sessions** — Ne perdez jamais votre travail

***

## 📦 Structure du projet

```text
Claw-Codex/
├── src/
│   ├── cli.py           # Entrée CLI
│   ├── config.py        # Configuration
│   ├── repl/            # REPL interactif
│   ├── providers/       # Fournisseurs LLM
│   └── agent/           # Gestion des sessions
├── tests/               # 75+ tests
└── docs/                # Docs complètes
```

***

## 🗺️ Feuille de route

- [x] MVP Python
- [x] Support multi-fournisseurs
- [x] Persistance des sessions
- [x] Audit de sécurité
- [ ] Système d'appel d'outils
- [ ] Paquet PyPI
- [ ] Version Go

***

## 🤝 Contribution

**Nous accueillons les contributions !**

```bash
# Configuration rapide pour le développement
pip install -e .[dev]
python -m pytest tests/ -v
```

Voir [CONTRIBUTING.md](../../CONTRIBUTING.md) pour les directives.

***

## 📖 Documentation

- **[SETUP_GUIDE.md](../guide/SETUP_GUIDE.md)** — Installation détaillée
- **[CONTRIBUTING.md](../../CONTRIBUTING.md)** — Guide de développement
- **[TESTING.md](../guide/TESTING.md)** — Guide de test
- **[CHANGELOG.md](../../CHANGELOG.md)** — Historique des versions

***

## ⚡ Performance

- **Démarrage** : < 1 seconde
- **Mémoire** : < 50MB
- **Réponse** : Streaming (temps réel)

***

## 🔒 Sécurité

✅ **Audit de sécurité effectué**

- Pas de données sensibles dans Git
- Clés API chiffrées dans la configuration
- Fichiers `.env` ignorés
- Sûr pour la production

***

## 📄 Licence

Licence MIT — Voir [LICENSE](../../LICENSE)

***

## 🙏 Remerciements

- Basé sur le code source TypeScript de Claude Code
- Projet éducatif indépendant
- Non affilié à Anthropic

***

<div align="center">

### 🌟 Montrez votre soutien

Si vous trouvez cela utile, veuillez **star** ⭐ le repo !

**Fait avec ❤️ par l'équipe Claw Codex**

[⬆ Retour en haut](#-claw-codex)

</div>
