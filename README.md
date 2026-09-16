# Bot de Sorteios para Discord

Bot gratuito com botão para participar, painel administrativo e sorteio automático por duas metas:

- quantidade de participantes; ou
- quantidade de membros no servidor.

## Recursos

- `/painel`: cria e publica um sorteio por um painel privado.
- Botões persistentes: continuam funcionando depois que o bot reinicia.
- Cargo obrigatório e cargo com entradas extras.
- Imagem, descrição, prêmio, quantidade de vencedores e canal configuráveis.
- `/encerrar`: encerra e sorteia imediatamente.
- `/sortear_novamente`: escolhe novos vencedores.
- Banco SQLite para não perder os sorteios.

## 1. Criar o bot no Discord

1. Acesse https://discord.com/developers/applications e crie uma aplicação.
2. Abra **Bot**, crie o bot e copie o token.
3. Em **Privileged Gateway Intents**, ative **Server Members Intent**.
4. Em **OAuth2 > URL Generator**, marque `bot` e `applications.commands`.
5. Dê ao bot as permissões: ver canais, enviar mensagens, inserir links e ler histórico.
6. Use a URL gerada para adicionar o bot ao servidor.

Nunca envie o token para ninguém e não coloque o arquivo `.env` no GitHub.

## 2. Rodar no computador

No PowerShell, dentro da pasta:

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
python bot.py
```

Cole o token em `DISCORD_TOKEN`. Depois use `/painel` no Discord.

## 3. Publicar no Railway

1. Envie estes arquivos para um repositório privado no GitHub.
2. Crie um projeto no Railway usando esse repositório.
3. Em **Variables**, adicione `DISCORD_TOKEN` com o token real.
4. Faça o deploy. O comando inicial já está no `railway.json`.

### Banco permanente no Railway

O disco normal pode ser apagado em um novo deploy. Para manter o banco:

1. Adicione um **Volume** no serviço e monte em `/data`.
2. Adicione a variável `DATABASE_PATH=/data/giveaways.db`.

## Como usar

1. Digite `/painel` (somente administradores).
2. Escolha **Participantes** ou **Membros no servidor**.
3. Selecione o canal.
4. Clique em **Configurar** e informe prêmio, meta e ganhadores.
5. Opcionalmente clique em **Avançado** para imagem e cargos.
6. Clique em **Publicar**.

Para IDs de cargos, ative o Modo Desenvolvedor em **Configurações > Avançado**, clique com o botão direito no cargo e use **Copiar ID**.
