# Privacidade e retenção — proposta para avaliação institucional

**Rascunho.** Este documento não estabelece, por si só, conformidade com a LGPD. A instituição responsável deve definir e aprovar os itens indicados abaixo antes do uso com documentos reais.

## Finalidade e dados

O Protocolo Eletrônico destina-se a receber, conferir e acompanhar documentos da equipe de enfermagem, incluindo atestados, ocorrências de ponto, trocas de plantão, férias e afastamentos. Pode conter dados de identificação, dados profissionais, documentos e dados de saúde, incluindo CID quando fornecido. Dados de saúde são dados pessoais sensíveis.

O hospital deve identificar o controlador, os responsáveis pelo tratamento, os destinatários autorizados, os fundamentos aplicáveis às diferentes atividades e o canal para solicitações dos titulares. Não se deve presumir que o consentimento seja a única hipótese aplicável.

## Proposta de retenção de 90 dias

O sistema mostra a data calculada como 90 dias corridos após o instante do recebimento, apenas como referência de revisão. **A exclusão automática não está ativada.** É necessário definir se o prazo se aplica a todas as categorias e situações, qual evento inicia a contagem e quais exceções, necessidades de guarda ou bloqueios existem.

Há uma rotina preparada para avaliação técnica que permanece desligada por padrão. Se futuramente autorizada e configurada pela TI com `P33_RETENCAO_AUTOMATICA=1`, ela exige também `P33_RETENCAO_ESCOPO=todos` ou `P33_RETENCAO_ESCOPO=concluidos`; neste último caso apenas protocolos com lançamento confirmado são abrangidos. O processo verifica o prazo ao iniciar e depois a cada hora enquanto o servidor estiver em execução. Ele elimina da base ativa os dados e anexos dos protocolos elegíveis e registra apenas o número, horário, operador `sistema` e motivo. Ele não implementa exceções individuais ou bloqueios de conservação; não o ative antes de definir e implementar as regras institucionais necessárias.

A LGPD não determina um prazo universal de 90 dias; sua aplicação exige análise das finalidades e eventuais obrigações de conservação. [Perguntas frequentes da ANPD](https://www.gov.br/anpd/pt-br/acesso-a-informacao/perguntas-frequentes) e [LGPD, artigos 15 e 16](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709compilado.htm).

## Exclusão

Na versão atual, a exclusão manual remove o protocolo, o documento e o histórico associado da base ativa e registra apenas número do protocolo, operador, horário e modalidade da exclusão em tabela separada. O identificador do operador pode ser dado pessoal; a instituição deve definir quem acessa esse registro e o prazo de guarda. A conta `local` usada em testes não identifica individualmente a pessoa que executou a ação.

A exclusão da base ativa não elimina automaticamente cópias em backups, arquivos exportados, e-mails ou aplicativos externos. Também não comprova eliminação física irrecuperável de arquivos ou de páginas livres do banco SQLite. A TI deve definir e testar prazos, restauração e descarte dessas cópias, além de como evitar a reintrodução de registros excluídos ao restaurar backups.

## Segurança e atendimento aos titulares

Antes do uso institucional, a TI e a instituição devem aprovar identidade e permissões de acesso, conexões seguras, armazenamento e backups protegidos, procedimentos de atendimento aos titulares, tratamento de incidentes e regras de retenção. O modo local sem login é apenas para avaliação e não diferencia usuários. Dados reais não devem ser usados nesse modo sem aprovação institucional.

**Preencher pela instituição:** controlador e contato; encarregado ou canal de privacidade; finalidades e fundamentos por tipo de documento; destinatários autorizados; prazo e exceções; controle de acesso; política de backups e exportações; procedimentos de exclusão e auditoria.
