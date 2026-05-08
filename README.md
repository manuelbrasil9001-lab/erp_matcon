# ERP MatCon — Deploy Railway (Backend) + Vercel (Frontend)

## Arquitetura

```
Vercel (frontend estático)  →  Railway (FastAPI + PostgreSQL)
https://erp-matcon.vercel.app   https://erp-matcon-api.railway.app
```

---

## PASSO 1 — Deploy do Backend no Railway

### 1.1 Criar conta e novo projeto
1. Acesse [railway.app](https://railway.app) → **Login com GitHub**
2. Clique em **New Project** → **Deploy from GitHub repo**
3. Selecione o repositório `erp-matcon-backend`

### 1.2 Adicionar banco PostgreSQL
1. No projeto Railway, clique em **+ New** → **Database** → **PostgreSQL**
2. O Railway injeta `DATABASE_URL` automaticamente ✅

### 1.3 Configurar variáveis de ambiente
No Railway → seu serviço → **Variables**, adicione:

| Variável | Valor |
|---|---|
| `JWT_SECRET` | cole uma string aleatória longa |
| `ALLOWED_ORIGINS` | `https://seu-frontend.vercel.app` |

> Gere o JWT_SECRET: `python -c "import secrets; print(secrets.token_hex(32))"`

### 1.4 Anotar a URL do Railway
Após o deploy, Railway mostra a URL pública, ex:
`https://erp-matcon-api.up.railway.app`

---

## PASSO 2 — Configurar o Frontend

Edite o arquivo `frontend/index.html`, linha que define `BASE`:

```javascript
const BASE = 'https://SEU-PROJETO.up.railway.app';  // ← troque aqui
```

Ou configure via painel Vercel (mais profissional):
1. No Vercel → Settings → Environment Variables
2. Adicione `API_URL` = `https://seu-projeto.up.railway.app`

---

## PASSO 3 — Deploy do Frontend no Vercel

### 3.1 Instalar Vercel CLI (opcional)
```bash
npm install -g vercel
```

### 3.2 Deploy
```bash
cd frontend
vercel --prod
```

Ou pelo painel:
1. [vercel.com](https://vercel.com) → **New Project** → **Import Git Repository**
2. Selecione `erp-matcon-frontend`
3. **Framework Preset**: Other
4. **Root Directory**: `frontend`
5. Clique em **Deploy**

---

## PASSO 4 — Atualizar CORS no Railway

Após ter a URL do Vercel (ex: `https://erp-matcon.vercel.app`), 
atualize a variável `ALLOWED_ORIGINS` no Railway:

```
ALLOWED_ORIGINS=https://erp-matcon.vercel.app
```

---

## Desenvolvimento Local

```bash
# Backend
pip install -r requirements.txt
python run.py

# Acesse: http://localhost:7821/frontend/index.html
# Login: admin@matcon.com.br / admin123
```

---

## Estrutura dos repositórios

```
erp-matcon-backend/          → deploy no Railway
├── backend/
│   ├── __init__.py          # App FastAPI
│   ├── database.py          # SQLite local / PostgreSQL produção
│   ├── auth_models.py       # Usuários e permissões
│   ├── routers/
│   │   ├── api.py           # Endpoints principais
│   │   └── auth_router.py   # Auth, login, usuários
│   └── services/
│       ├── auth_service.py  # JWT, hash, permissões
│       └── bling_service.py # Integração Bling API v3
├── tests/                   # 106 testes
├── requirements.txt
├── Procfile                 # Railway start command
├── railway.toml
├── runtime.txt
└── .env.example

erp-matcon-frontend/         → deploy no Vercel
├── index.html               # App completo em um arquivo
└── vercel.json              # Configuração SPA
```
# erp_matcon
