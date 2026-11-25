# Protocolo Eletrônico

MVP da P33 Systems para a supervisão de enfermagem receber, conferir e acompanhar atestados, ocorrências de ponto, trocas de plantão, férias e afastamentos. O sistema registra a entrega assim que grava o arquivo, gera um código de protocolo e tenta preencher os campos a partir do documento. A leitura depende do conteúdo e do formato; todos os dados sugeridos devem ser conferidos.

## Uso operacional local

Para habilitar o envio direto de e-mails com o documento anexado, configure no processo servidor `P33_SMTP_HOST`, `P33_SMTP_PORTA`, `P33_SMTP_USUARIO`, `P33_SMTP_SENHA` e, se diferente do usuário, `P33_SMTP_REMETENTE`. Defina `P33_SMTP_SEGURANCA` como `starttls` (padrão) ou `ssl`. Use uma conta autorizada e guarde as credenciais fora do código e do controle de versão. Consulte a TI sobre os requisitos de autenticação do provedor. O servidor SMTP confirma a aceitação, não a entrega final; confira a entrega antes de registrar o encaminhamento.

O modo padrão exige contas individuais e grava no volume persistente do Docker. Na primeira execução, crie as contas antes de iniciar o serviço:

```powershell
docker compose build
docker compose run --rm p33 python app.py --criar-usuario supervisao --perfil supervisao
docker compose run --rm p33 python app.py --criar-usuario recepcao --perfil recepcao
docker compose up
```

Defina uma senha individual com pelo menos 12 caracteres em cada solicitação. Acesse [P33 Protocolos](http://localhost:8080), entre com sua conta e use **Novo recebimento** para enviar uma foto, PDF ou imagem de QR Code. A leitura sugere os campos e a supervisão deve compará-los com o documento original antes de avançar o protocolo.

O banco operacional fica no volume `protocolos`; não remova o volume para preservar os registros. A porta fica limitada ao próprio computador. Para uso por uma equipe em rede, configure antes um proxy HTTPS, controle de acesso à rede, backups protegidos e uma política de retenção com a TI. Não exponha diretamente a porta do aplicativo à internet.

## Demonstração no Windows

Para iniciar a demonstração com dados fictícios, execute `docker compose run --rm p33 python app.py --demo`. Ela usa um banco separado (`p33-demo.sqlite3`) e habilita o botão **Entrar na demonstração**. Use somente os documentos sintéticos de `exemplos/`. Para parar o serviço, pressione Ctrl+C no terminal.

## Experimente o fluxo

| Arquivo | O que observar |
|---|---|
| `Atestado_Foto.png` | OCR identifica nome, matrícula, data, tipo e dias |
| `Ocorrencia_de_Ponto.pdf` | O texto do PDF preenche a identificação da ocorrência |
| `Atestado_Escaneado.pdf` | OCR lê uma página que contém somente uma imagem |
| `QR_Code.png` | QR com dados estruturados preenche os campos de conferência |

Abra o original, compare os campos, informe o setor e marque **Conferi os dados com o original**. Em seguida, registre o encaminhamento com o destino e confirme o lançamento com uma referência. Pelo painel, filtre por status, busque o código ou exporte a relação em CSV como supervisão. Envie o mesmo arquivo outra vez para ver o código do recebimento já existente.

Na lista, os botões **Excel** e **PDF** exportam os protocolos que correspondem aos filtros ativos, até 900 por vez. Os relatórios contêm protocolo, documento, nome e matrícula do colaborador, setor, recebimento e status; não incluem CPF, dados clínicos ou anexos. O CSV da navegação continua exportando a relação geral sem nome.

## Regras do MVP

- Perfis de recebimento e supervisão. Apenas a supervisão consulta a identificação completa, baixa os anexos ou atualiza as etapas.
- PDF, JPEG e PNG de até 12 MB. O sistema lê até as cinco primeiras páginas e guarda o original completo. O arquivo e o evento de recebimento são persistidos antes da leitura.
- A leitura roda no servidor local por Poppler, Tesseract e ZBar. O sistema reconhece campos rotulados como nome, CPF, matrícula e data. QR com chaves estruturadas ou campos rotulados pode preencher os mesmos dados. QR com URL sinaliza conferência pelo canal oficial.
- Documentos borrados, com campos ausentes ou com modelos diferentes podem exigir digitação. A supervisão revisa a leitura e pode salvá-la como rascunho. O CPF é opcional e, quando informado, passa pela validação dos dígitos verificadores na conferência.
- Datas de início e fim, CID, médico, local e token são sugeridos quando identificados no documento; datas e diagnóstico não são inferidos. O QR é tratado como fonte de dados, não como prova de autenticidade. A supervisão precisa conferir as sugestões e validar o atestado pelo emissor.
- Para ocorrência de ponto, é possível registrar chapa, setor, até dez linhas de data, ocorrência e justificativa, e as tratativas de compensação, abono, descontos, troca de plantão/folga e horas extras. O leitor só sugere uma linha quando encontra os campos rotulados de forma clara; marcações e assinaturas da foto não são interpretadas automaticamente.
- Um hash detecta reenvio de arquivos idênticos. Fotografias distintas de um mesmo documento podem gerar protocolos diferentes. Cada avanço registra usuário e horário. O CSV exclui CPF, nome e anexos.
- O encaminhamento e a confirmação do lançamento registram ações realizadas pela equipe fora do aplicativo. Futuramente, uma integração autorizada pode consumir registros conferidos e devolver uma referência de lançamento. A matrícula, o identificador interno, o destino e a referência já são persistidos para esse ponto de integração.

## Execução direta no Linux

No Windows, instale as dependências locais com `python -m pip install -r requirements-windows.txt`. Elas permitem ler texto de PDFs digitais e QR Codes sem Poppler ou ImageMagick. Para OCR de imagens e PDFs digitalizados, também é necessário instalar o Tesseract com os dados do idioma português e disponibilizá-lo no PATH. Confira os campos sugeridos com o original.

Requer Python 3.12, `poppler-utils`, `tesseract-ocr`, `tesseract-ocr-por`, `imagemagick`, `libzbar0` e `tzdata`. Com os pacotes instalados, crie contas com `python3 app.py --criar-usuario LOGIN --perfil supervisao` (e repita para recepção), depois rode `python3 app.py`. O parâmetro `--demo` é opcional e usa um banco separado. `--port` permite escolher outra porta; `P33_OCR_LANG` seleciona o idioma instalado, com padrão `por`.

## Preparação do piloto institucional

O modo operacional remove o acesso automático de demonstração, mas não substitui a aprovação institucional. Antes de receber documentos reais, a TI e a supervisão devem aprovar o ambiente, as permissões, a política de retenção, os backups e o procedimento de tratamento dos documentos. Use HTTPS com `P33_SECURE_COOKIE=1`, contas individuais, armazenamento protegido e backup consistente do banco junto com os anexos. O servidor HTTP incluído deve ficar atrás de um proxy HTTPS em qualquer acesso pela rede. O SQLite guarda os anexos no próprio banco.

## Estrutura

- `app.py` — servidor, sessões, perfis, protocolos, auditoria e exportação.
- `leitura.py` — extração de PDF, OCR, decodificação de QR e seleção de campos.
- `web/` — painel responsivo e fluxo de conferência.
- `exemplos/` — quatro documentos sintéticos para demonstração.

## Render

`render.yaml` é uma configuração de avaliação. O armazenamento SQLite do plano Free pode ser perdido quando a instância reiniciar; não use esse ambiente para documentos reais. Para operação institucional, escolha armazenamento persistente e revise segurança, retenção e acesso com a TI antes de publicar.
