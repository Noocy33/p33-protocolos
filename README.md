# P33 Protocolos

MVP da P33 Systems para a supervisão de enfermagem receber, conferir e acompanhar atestados e ocorrências de ponto. O sistema registra a entrega assim que grava o arquivo, gera um código de protocolo e tenta preencher os campos a partir do documento.

## Demonstração no Windows

1. Extraia o ZIP, abra o Docker Desktop e aguarde o serviço iniciar.
2. Abra a pasta extraída no VS Code. No terminal da pasta `p33-protocolos`, execute:

   ```powershell
   docker compose up --build
   ```

3. Acesse [P33 Protocolos](http://localhost:8080) e clique em **Entrar na demonstração**. O endereço abre no computador em que o comando estiver rodando.
4. Clique em **Novo recebimento** e escolha um arquivo da pasta `exemplos`.

A conta `supervisao` tem acesso à conferência e ao histórico. A conta `recepcao` registra entregas e consulta a situação dos protocolos. O botão de demonstração abre uma sessão de supervisão com dados fictícios. O banco da demonstração é separado do banco de operação e persiste no volume do Docker. Use apenas os exemplos fictícios nesse modo. Para parar o serviço, pressione Ctrl+C no terminal.

## Experimente o fluxo

| Arquivo | O que observar |
|---|---|
| `Atestado_Foto.png` | OCR identifica nome, matrícula, data, tipo e dias |
| `Ocorrencia_de_Ponto.pdf` | O texto do PDF preenche a identificação da ocorrência |
| `Atestado_Escaneado.pdf` | OCR lê uma página que contém somente uma imagem |
| `QR_Code.png` | QR com dados estruturados preenche os campos de conferência |

Abra o original, compare os campos, informe o setor e marque **Conferi os dados com o original**. Em seguida, registre o encaminhamento com o destino e confirme o lançamento com uma referência. Pelo painel, filtre por status, busque o código ou exporte a relação em CSV como supervisão. Envie o mesmo arquivo outra vez para ver o código do recebimento já existente.

## Regras do MVP

- Perfis de recebimento e supervisão. Apenas a supervisão consulta a identificação completa, baixa os anexos ou atualiza as etapas.
- PDF, JPEG e PNG de até 12 MB. O sistema lê até as cinco primeiras páginas e guarda o original completo. O arquivo e o evento de recebimento são persistidos antes da leitura.
- A leitura roda no servidor local por Poppler, Tesseract e ZBar. O sistema reconhece campos rotulados como nome, CPF, matrícula e data. QR com chaves estruturadas ou campos rotulados pode preencher os mesmos dados. QR com URL sinaliza conferência pelo canal oficial.
- Documentos borrados, com campos ausentes ou com modelos diferentes podem exigir digitação. A supervisão revisa a leitura e pode salvá-la como rascunho. O CPF é opcional e, quando informado, passa pela validação dos dígitos verificadores na conferência.
- Um hash detecta reenvio de arquivos idênticos. Fotografias distintas de um mesmo documento podem gerar protocolos diferentes. Cada avanço registra usuário e horário. O CSV exclui CPF, nome e anexos.
- O encaminhamento e a confirmação do lançamento registram ações realizadas pela equipe fora do aplicativo. Futuramente, uma integração autorizada pode consumir registros conferidos e devolver uma referência de lançamento. A matrícula, o identificador interno, o destino e a referência já são persistidos para esse ponto de integração.

## Execução direta no Linux

Requer Python 3.12, `poppler-utils`, `tesseract-ocr`, `tesseract-ocr-por`, `imagemagick`, `libzbar0` e `tzdata`. Com os pacotes instalados, rode `python3 app.py --demo`. O parâmetro `--port` permite escolher outra porta. A variável `P33_OCR_LANG` seleciona o idioma instalado. O padrão é `por`.

## Preparação do piloto institucional

A versão foi preparada para demonstração local. Antes de receber documentos reais, a TI e a supervisão devem aprovar o ambiente, as permissões, a política de retenção, os backups e o procedimento de tratamento dos documentos. Use contas individuais, volume protegido e uma rede interna com proxy HTTPS, limites de acesso e de solicitações. O servidor incluído é destinado à demonstração e precisa de revisão para implantação institucional. O SQLite guarda os anexos no próprio banco. Planeje proteção em repouso e backup consistente do banco junto com a TI.

Para preparar um banco vazio com Docker em avaliação técnica, execute:

```powershell
docker compose run --rm p33 python app.py --criar-usuario supervisao --perfil supervisao
docker compose run --rm p33 python app.py --criar-usuario recepcao --perfil recepcao
```

Crie senhas pelo terminal e configure no Compose `command: ["python", "app.py"]` para iniciar esse banco. Em ambiente com HTTPS, configure `P33_SECURE_COOKIE=1`. As duas bases são arquivos distintos no volume. Ao usar o Docker na estação, a porta publicada fica restrita à própria estação.

## Estrutura

- `app.py` — servidor, sessões, perfis, protocolos, auditoria e exportação.
- `leitura.py` — extração de PDF, OCR, decodificação de QR e seleção de campos.
- `web/` — painel responsivo e fluxo de conferência.
- `exemplos/` — quatro documentos sintéticos para demonstração.

## Demonstração no Render

O arquivo `render.yaml` descreve um serviço web Docker no plano Free. No Render, crie um Blueprint com este repositório para usar essa configuração. O container inicia em modo demonstração. Use apenas os documentos fictícios de `exemplos/`. No plano Free, os registros gravados no SQLite podem ser perdidos quando a instância reiniciar.
