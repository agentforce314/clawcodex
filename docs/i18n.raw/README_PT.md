<div align="center">

[English](../../README.md) | [中文](README_ZH.md) | [Français](README_FR.md) | [Русский](README_RU.md) | [हिन्दी](README_HI.md) | [العربية](README_AR.md) | **Português**

# 🚀 Claude Code Python

**Uma Reimplementação Completa em Python Baseada no Código Fonte Real do Claude Code**

*Do Código Fonte TypeScript → Reconstruído em Python com ❤️*

***

[![GitHub stars](https://img.shields.io/github/stars/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=yellow)](https://github.com/GPT-AGI/Claw-Codex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/GPT-AGI/Claw-Codex?style=for-the-badge&logo=github&color=blue)](https://github.com/GPT-AGI/Claw-Codex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)

**🔥 Desenvolvimento Ativo • Novos Recursos Semanalmente 🔥**

</div>

***

## 🎯 O Que É Isso?

**Claw Codex** é uma **reescrita completa em Python** do Claude Code, baseada no **código fonte TypeScript real**.

### ⚠️ Importante: Isso NÃO É Apenas Código Fonte

**Diferente do código fonte TypeScript vazado**, Claw Codex é uma **ferramenta CLI totalmente funcional**:

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

**CLI Real • Uso Real • Comunidade Real**

</div>

- ✅ **CLI Funcional** — Não é apenas código, mas uma ferramenta de linha de comando totalmente funcional que você pode usar hoje
- ✅ **Baseado no Código Real** — Portado da implementação TypeScript real do Claude Code
- ✅ **Máxima Fidelidade** — Preserva a arquitetura original enquanto otimiza
- ✅ **Python Nativo** — Código Python limpo e idiomático com anotações de tipo completas
- ✅ **Amigável ao Usuário** — Configuração fácil, REPL interativo, documentação abrangente
- ✅ **Continuamente Melhorado** — Tratamento de erros aprimorado, testes, documentação

**🚀 Experimente agora! Faça fork, modifique, torne seu! Pull requests são bem-vindos!**

***

## ⭐ Star History

<a href="https://www.star-history.com/?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=GPT-AGI%2FClaw-Codex&type=date&legend=top-left" />
 </picture>
</a>

## ✨ Recursos

### Suporte Multi-Provedor

```python
providers = ["Anthropic Claude", "OpenAI GPT", "Zhipu GLM"]  # + fácil de estender
```

### REPL Interativo

```text
>>> Olá!
Assistant: Oi! Sou o Claw Codex, uma reimplementação em Python...

>>> /help         # Mostrar comandos
>>> /             # Mostrar comandos e skills
>>> /save         # Salvar sessão
>>> /multiline    # Modo multilinha
>>> Tab           # Auto-completar
>>> /explain-code qsort.py   # Executar um skill
```

### Skills (Slash Commands)

See [README.md](../../README.md#skills-slash-commands) for a quick tutorial on creating skills under `.clawcodex/skills/<skill-name>/SKILL.md`.

### CLI Completo

```bash
clawcodex              # Iniciar REPL
clawcodex login        # Configurar API
clawcodex --version    # Verificar versão
clawcodex config       # Ver configurações
```

***

## 📊 Status

| Componente          | Status     | Quantidade |
| ------------------- | ---------- | ---------- |
| Comandos            | ✅ Completo | 150+       |
| Ferramentas         | ✅ Completo | 100+       |
| Cobertura de Testes | ✅ 90%+     | 75+ testes |
| Documentação        | ✅ Completa | 10+ docs   |

***

## 🚀 Início Rápido

### Instalar

```bash
git clone https://github.com/GPT-AGI/Claw-Codex.git
cd Claw-Codex

# Criar venv (uv recomendado)
uv venv --python 3.11
source .venv/bin/activate

# Instalar
uv pip install -r requirements.txt
```

### Configurar

#### Opção 1: Interativo (Recomendado)

```bash
python -m src.cli login
```

Este processo irá:

1. pedir que você escolha um provedor: anthropic / openai / glm
2. pedir a chave API desse provedor
3. salvar opcionalmente uma URL base personalizada
4. salvar opcionalmente um modelo padrão
5. definir o provedor selecionado como padrão

O arquivo de configuração é salvo em `~/.clawcodex/config.json`. Exemplo de estrutura:

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

### Executar

```bash
python -m src.cli          # Iniciar REPL
python -m src.cli --help   # Mostrar ajuda
```

**É isso!** Comece a conversar com IA em 3 passos.

***

## 💡 Uso

### Comandos REPL

| Comando      | Descrição                 |
| ------------ | ------------------------- |
| `/help`      | Mostrar todos os comandos |
| `/save`      | Salvar sessão             |
| `/load <id>` | Carregar sessão           |
| `/multiline` | Alternar modo multilinha  |
| `/clear`     | Limpar histórico          |
| `/exit`      | Sair do REPL              |

### Exemplo de Sessão

![Exemplo de Sessão](../../assets/claw-codex-tool-skill-json.png)

***

## 🎓 Por Que Claw Codex?

### Baseado no Código Fonte Real

- **Não é um clone** — Portado da implementação TypeScript real
- **Fidelidade arquitetural** — Mantém padrões de design comprovados
- **Melhorias** — Melhor tratamento de erros, mais testes, código mais limpo

### Python Nativo

- **Dicas de tipo** — Anotações de tipo completas
- **Python moderno** — Usa recursos 3.10+
- **Idiomático** — Código Python limpo

### Focado no Usuário

- **Configuração em 3 passos** — Clonar, configurar, executar
- **Configuração interativa** — `clawcodex login` guia você
- **REPL rico** — Completar com tab, destaque de sintaxe
- **Persistência de sessão** — Nunca perca seu trabalho

***

## 📦 Estrutura do Projeto

```text
Claw-Codex/
├── src/
│   ├── cli.py           # Entrada CLI
│   ├── config.py        # Configuração
│   ├── repl/            # REPL interativo
│   ├── providers/       # Provedores LLM
│   └── agent/           # Gerenciamento de sessão
├── tests/               # 75+ testes
└── docs/                # Docs completos
```

***

## 🗺️ Roteiro

- [x] MVP Python
- [x] Suporte multi-provedor
- [x] Persistência de sessão
- [x] Auditoria de segurança
- [ ] Sistema de chamada de ferramentas
- [ ] Pacote PyPI
- [ ] Versão Go

***

## 🤝 Contribuindo

**Nós acolhemos contribuições!**

```bash
# Configuração rápida de dev
pip install -e .[dev]
python -m pytest tests/ -v
```

Veja [CONTRIBUTING.md](../../CONTRIBUTING.md) para diretrizes.

***

## 📖 Documentação

- **[SETUP_GUIDE.md](../guide/SETUP_GUIDE.md)** — Instalação detalhada
- **[CONTRIBUTING.md](../../CONTRIBUTING.md)** — Guia de desenvolvimento
- **[TESTING.md](../guide/TESTING.md)** — Guia de testes
- **[CHANGELOG.md](../../CHANGELOG.md)** — Histórico de versões

***

## ⚡ Performance

- **Inicialização**: < 1 segundo
- **Memória**: < 50MB
- **Resposta**: Streaming (tempo real)

***

## 🔒 Segurança

✅ **Auditoria de Segurança Realizada**

- Sem dados sensíveis no Git
- Chaves API criptografadas na configuração
- Arquivos `.env` ignorados
- Seguro para produção

***

## 📄 Licença

Licença MIT — Veja [LICENSE](../../LICENSE)

***

## 🙏 Agradecimentos

- Baseado no código fonte TypeScript do Claude Code
- Projeto educacional independente
- Não afiliado à Anthropic

***

<div align="center">

### 🌟 Mostre Seu Apoio

Se você acha isso útil, por favor dê uma **star** ⭐ no repo!

**Feito com ❤️ pela equipe Claw Codex**

[⬆ Voltar ao Topo](#-claw-codex)

</div>
