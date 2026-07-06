# Projeto Roteiros de Viagem com IA

Este projeto nasceu como uma prova de conceito para transformar planejamento de viagens em uma experiência conversacional, integrada ao Telegram e apoiada por IA. A ideia principal é entregar roteiros personalizados de forma natural, com geração automática de conteúdo e distribuição em texto e PDF.

Como visão de evolução, a proposta é transformar essa base em um ecossistema de conteúdo digital, incluindo um blog alimentado por matérias geradas por IA, com foco em automação, personalização e escalabilidade.

## O que o projeto faz

- Recebe informações do usuário pelo Telegram.
- Coleta destino, datas, ritmo de viagem e preferências.
- Gera um roteiro estruturado com apoio de IA.
- Monta uma resposta amigável e cria um PDF para envio.
- Pode enviar o roteiro por e-mail, quando configurado.

## Stack utilizada

- Python
- python-telegram-bot
- OpenAI API
- ReportLab / geração de PDF
- Render para deploy

## Arquitetura do projeto

- `bot.py`: fluxo de conversa e integração com o Telegram.
- `services/trip_logic_wikivoyage.py`: orquestração do roteiro e validação do fluxo.
- `services/openai_planner.py`: construção do prompt e integração com a API da OpenAI.
- `services/presenter.py`: transforma o roteiro em texto legível e estruturado.
- `services/pdf_service.py`: gera o PDF final com marca visual.

## Como rodar localmente

1. Crie um ambiente virtual.
2. Instale as dependências:
   `pip install -r requirements.txt`
3. Copie o arquivo `.env.example` para `.env` e preencha os valores.
4. Execute:
   `python bot.py`

## Variáveis de ambiente

As variáveis principais são:

- `TELEGRAM_BOT_TOKEN`: token do bot no Telegram.
- `OPENAI_API_KEY`: chave da OpenAI para gerar roteiros.
- `OPENAI_MODEL`: modelo usado para geração (padrão `gpt-4o-mini`).
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`: envio de e-mail.
- `PDF_WATERMARK_IMAGE_PATH`: caminho para marca no PDF.

## Deploy

Configuração básica para Render:

- Tipo de serviço: `Worker`
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

## Segurança

- O arquivo `.env` não é versionado e deve ficar apenas localmente.
- Credenciais reais nunca devem ser adicionadas ao repositório.
- Use o arquivo `.env.example` como referência para configurar o ambiente.

## Destaque para entrevista

Este projeto demonstra habilidades em:

- Automação de fluxos conversacionais.
- Integração com APIs externas.
- Geração de conteúdo com IA.
- Transformação de dados em produtos úteis para o usuário.
- Estruturação de um projeto com potencial de evolução para produtos de conteúdo e automação.
