const $ = id => document.getElementById(id)
const status = {recebido:'A conferir', conferido:'Conferido', encaminhado:'Encaminhado', baixado:'Lançamento confirmado'}
const types = {atestado:'Atestado', ponto:'Ocorrência de ponto', troca:'Troca de plantão', ferias:'Férias', afastamento:'Afastamento'}
const tratativasPonto = {compensacao:'Compensação de horas', abonar:'Abonar', descontar_dia:'Descontar dia do salário', descontar_horas:'Descontar horas do salário', troca:'Troca de plantão / folga', horas_extras:'Horas extras'}
function lerLista(value) { try { const result = JSON.parse(value || '[]'); return Array.isArray(result) ? result : [] } catch { return [] } }
const fieldLabels = {nome:'nome', cpf:'CPF', matricula:'matrícula', setor:'setor', tipo:'tipo', data:'data do documento', dias:'dias', data_inicio:'início', data_fim:'fim', cid:'CID', medico:'médico(a)', local_atestado:'local do atendimento', token_atestado:'código do documento', outro_profissional:'outro profissional', matricula_outro:'matrícula do outro profissional', data_troca:'data do plantão', hora_inicio:'horário de início', hora_fim:'horário de fim', ocorrencias_ponto:'ocorrência de ponto'}
const leituraMensagem = result => {
  const fields = (result.campos_sugeridos || Object.keys(result.campos || {})).map(key => fieldLabels[key]).filter(Boolean)
  return [result.metodos.length ? `Leitura: ${result.metodos.join(', ')}` : '', fields.length ? `Campos sugeridos: ${fields.join(', ')}. Confira com o original.` : 'Nenhum campo foi identificado automaticamente.', ...result.avisos].filter(Boolean).join(' · ')
}
const modal = $('modal')
let account, rows = [], timer, visibleIds = []
let retencaoConfig = {retencao_automatica:false, retencao_escopo:''}
let cameraStream
function stopCamera() {
  cameraStream?.getTracks().forEach(track => track.stop())
  cameraStream = null
}
modal.addEventListener('close', stopCamera)
const dateTime = new Intl.DateTimeFormat('pt-BR', {dateStyle:'short',timeStyle:'short',timeZone:'America/Sao_Paulo'})
const dateParts = new Intl.DateTimeFormat('en-CA', {year:'numeric',month:'2-digit',day:'2-digit',timeZone:'America/Sao_Paulo'})
function receivedDate(value) {
  const parts = Object.fromEntries(dateParts.formatToParts(new Date(value)).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}
const h = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))
const normalize = value => String(value ?? '').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase()
const formObject = form => Object.fromEntries(new FormData(form))
const supervisor = () => account?.perfil === 'supervisao'
const send = (path, data = {}) => api(path, {method:'POST', body:JSON.stringify(data), headers:{'Content-Type':'application/json'}})

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{...options.headers, 'X-P33-Request':'1'}})
  const data = await response.json()
  if (!response.ok) {
    if (response.status === 401 && account && path !== '/api/login') showLogin()
    const error = new Error(data.erro || 'Falha na solicitação.')
    error.data = data
    error.status = response.status
    throw error
  }
  return data
}
function notify(message) {
  $('toast').textContent = message
  $('toast').hidden = false
  clearTimeout(timer)
  timer = setTimeout(() => { $('toast').hidden = true }, 6500)
}
function showLogin() {
  account = null
  rows = []
  $('rows').replaceChildren()
  $('modal-body').replaceChildren()
  if (modal.open) modal.close()
  $('workspace').hidden = true
  $('login').hidden = false
  $('login-form').reset()
}
async function enter(user) {
  account = user
  $('login').hidden = true
  $('workspace').hidden = false
  $('account-name').textContent = user.login
  $('avatar').textContent = user.login[0].toUpperCase()
  $('role').textContent = supervisor() ? 'Supervisão' : 'Recebimento'
  $('export').hidden = !supervisor()
  $('export-actions').hidden = !supervisor()
  $('employee-col').hidden = !supervisor()
  $('search').placeholder = supervisor() ? 'Protocolo, matrícula, nome ou setor' : 'Protocolo ou setor'
  await refresh()
}
async function refresh() {
  rows = await api('/api/protocolos')
  const sector = $('filter-sector').value
  const sectors = [...new Set(rows.map(row => row.setor).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'pt-BR'))
  $('filter-sector').innerHTML = '<option value="">Todos os setores</option>' + sectors.map(value=>`<option value="${h(value)}">${h(value)}</option>`).join('')
  $('filter-sector').value = sectors.includes(sector) ? sector : ''
  draw()
}
function draw() {
  const selected = $('status').value
  $('metrics').innerHTML = Object.entries(status).map(([key,label]) =>
    `<button class="metric" data-status="${key}" aria-pressed="${selected === key}"><span>${label}</span><strong>${rows.filter(row=>row.status===key).length}</strong></button>`).join('')
  const query = normalize($('search').value.trim())
  const type = $('type').value
  const sector = $('filter-sector').value
  const from = $('filter-from').value, to = $('filter-to').value
  const invalidPeriod = Boolean(from && to && from > to)
  $('filter-error').hidden = !invalidPeriod
  $('filter-error').textContent = invalidPeriod ? 'A data inicial deve ser anterior ou igual à data final.' : ''
  const filtered = rows.filter(r => !invalidPeriod && (!selected || r.status === selected) && (!type || r.tipo === type) &&
    (!sector || r.setor === sector) && (!from || receivedDate(r.criado) >= from) && (!to || receivedDate(r.criado) <= to) &&
    [r.codigo,r.nome,r.matricula,r.setor].some(value => normalize(value).includes(query)))
  visibleIds = filtered.map(row => row.id)
  $('export-excel').disabled = $('export-pdf').disabled = invalidPeriod || !visibleIds.length
  $('export-hint').hidden = !supervisor() || visibleIds.length > 0
  $('export-hint').textContent = invalidPeriod ? 'Corrija o período para exportar.' : rows.length ? 'Nenhum protocolo corresponde aos filtros. Ajuste ou limpe os filtros para exportar.' : 'Para exportar em Excel ou PDF, primeiro registre um documento em Novo recebimento.'
  $('summary').textContent = `${filtered.length} de ${rows.length} registros`
  $('empty').hidden = filtered.length !== 0
  $('rows').innerHTML = filtered.map(r => `<tr>
    <td><span class="cell-code">${h(r.codigo)}</span><span class="cell-note">${h(r.origem)}</span></td>
    <td>${h(types[r.tipo] || 'Tipo a identificar')}</td>
    ${supervisor() ? `<td>${h(r.nome || 'A identificar')}<span class="cell-note">${h(r.matricula)}</span></td>` : ''}
    <td>${h(r.setor || '—')}</td><td>${dateTime.format(new Date(r.criado))}</td>
    <td><span class="badge ${h(r.status)}">${h(status[r.status])}</span></td>
    <td>${supervisor() ? `<button type="button" data-id="${r.id}" aria-label="Abrir ${h(r.codigo)}">Abrir</button>` : ''}</td></tr>`).join('')
}
function openModal(title, content) {
  stopCamera()
  $('modal-title').textContent = title
  $('modal-body').innerHTML = content
  $('modal-error').textContent = ''
  if (!modal.open) modal.showModal()
}
function fields(row = {}) {
  return `<label class="full">Tipo de documento<select name="tipo"><option value="">Selecione</option>
    ${Object.entries(types).map(([key,label])=>`<option value="${key}" ${row.tipo===key?'selected':''}>${label}</option>`).join('')}</select></label>
    <label class="full">Nome do colaborador<input name="nome" maxlength="120" value="${h(row.nome)}"></label>
    <label>Matrícula / chapa<input name="matricula" maxlength="30" value="${h(row.matricula)}"></label>
    <label>CPF, se constar<input name="cpf" maxlength="18" inputmode="numeric" value="${h(row.cpf)}"></label>
    <label>Setor<input name="setor" maxlength="80" value="${h(row.setor)}"></label>
    <label>Data do documento<input name="data" type="date" value="${h(row.data)}"></label>
    <label data-categorias="atestado ferias afastamento">Dias informados, se aplicável<input name="dias" inputmode="numeric" maxlength="3" value="${h(row.dias)}"></label>
    <label data-categorias="atestado ferias afastamento">Data de início<input name="data_inicio" type="date" value="${h(row.data_inicio)}"></label>
    <label data-categorias="atestado ferias afastamento">Data de fim<input name="data_fim" type="date" value="${h(row.data_fim)}"></label>
    <label data-categorias="atestado afastamento">CID, se constar<input name="cid" maxlength="30" value="${h(row.cid)}"></label>
    <label data-categorias="atestado afastamento">Médico(a)<input name="medico" maxlength="120" value="${h(row.medico)}"></label>
    <label data-categorias="atestado afastamento">Local do atendimento<input name="local_atestado" maxlength="160" value="${h(row.local_atestado)}"></label>
    <label data-categorias="atestado afastamento">Token do documento<input name="token_atestado" maxlength="200" value="${h(row.token_atestado)}"></label>
    <label data-categorias="troca">Outro profissional<input name="outro_profissional" maxlength="120" value="${h(row.outro_profissional)}"></label>
    <label data-categorias="troca">Matrícula do outro profissional<input name="matricula_outro" maxlength="30" value="${h(row.matricula_outro)}"></label>
    <label data-categorias="troca">Data do plantão<input name="data_troca" type="date" value="${h(row.data_troca)}"></label>
    <label data-categorias="troca ponto">Horário de início<input name="hora_inicio" type="time" value="${h(row.hora_inicio)}"></label>
    <label data-categorias="troca ponto">Horário de fim<input name="hora_fim" type="time" value="${h(row.hora_fim)}"></label>
    <div class="full ponto-campos" data-categorias="ponto">
      <h3>Ocorrências de ponto</h3><p class="hint">Informe uma linha para cada data, ocorrência e justificativa.</p>
      <input type="hidden" name="ocorrencias_ponto" value="${h(row.ocorrencias_ponto)}">
      <div class="lista-ocorrencias"></div><button type="button" class="adicionar-ocorrencia">Adicionar ocorrência</button>
      <h3>Tratativa do ponto — supervisão</h3><p class="hint">Marque somente as opções conferidas no documento.</p>
      <input type="hidden" name="tratativas_ponto" value="${h(row.tratativas_ponto)}">
      <div class="tratativas-lista">${Object.entries(tratativasPonto).map(([key,label])=>`<label class="check"><input type="checkbox" data-tratativa="${key}">${label}</label>`).join('')}</div>
      <div class="form-grid"><label>Troca de plantão / folga de<input name="troca_ponto_de" type="date" value="${h(row.troca_ponto_de)}"></label>
      <label>Troca de plantão / folga para<input name="troca_ponto_para" type="date" value="${h(row.troca_ponto_para)}"></label></div>
    </div>
    <label class="full">Observações<input name="detalhes" maxlength="1000" value="${h(row.detalhes)}"></label>`
}
function configurarCampos(form) {
  const select = form.elements.tipo
  if (!select) return
  const update = () => form.querySelectorAll('[data-categorias]').forEach(el => { el.hidden = !el.dataset.categorias.split(' ').includes(select.value) })
  select.addEventListener('change', update)
  update()
  const group = form.querySelector('.ponto-campos')
  if (!group) return
  const lista = group.querySelector('.lista-ocorrencias')
  const linhas = lerLista(form.elements.ocorrencias_ponto.value)
  const saved = lerLista(form.elements.tratativas_ponto.value)
  group.querySelectorAll('[data-tratativa]').forEach(box => { box.checked = saved.includes(box.dataset.tratativa) })
  function sincronizar() {
    const values = [...lista.querySelectorAll('.ocorrencia-linha')].map(item => Object.fromEntries(['data','ocorrencia','justificativa'].map(key => [key,item.querySelector(`[data-campo="${key}"]`).value.trim()])))
    form.elements.ocorrencias_ponto.value = JSON.stringify(values.filter(item => Object.values(item).some(Boolean)))
    form.elements.tratativas_ponto.value = JSON.stringify([...group.querySelectorAll('[data-tratativa]:checked')].map(box=>box.dataset.tratativa))
  }
  function adicionar(item = {}) {
    if (lista.children.length >= 10) { notify('O limite é de 10 ocorrências por protocolo.'); return }
    const line = document.createElement('div')
    line.className = 'ocorrencia-linha form-grid'
    line.innerHTML = `<label>Data da ocorrência<input type="date" data-campo="data" value="${h(item.data)}"></label><label>Ocorrência<input data-campo="ocorrencia" maxlength="200" value="${h(item.ocorrencia)}"></label><label class="full">Justificativa<input data-campo="justificativa" maxlength="500" value="${h(item.justificativa)}"></label><button type="button" class="remover-ocorrencia">Remover linha</button>`
    line.querySelector('.remover-ocorrencia').onclick = () => { line.remove(); sincronizar() }
    lista.append(line)
  }
  ;(linhas.length ? linhas : [{}]).forEach(adicionar)
  group.querySelector('.adicionar-ocorrencia').onclick = () => { adicionar(); sincronizar() }
  group.addEventListener('input', sincronizar)
  group.addEventListener('change', sincronizar)
}
async function action(button, fn) {
  const controls = [...(button.form?.querySelectorAll('button') || [button])]
  $('modal-error').textContent = ''
  controls.forEach(control => { control.disabled = true })
  try { await fn() }
  catch (error) {
    if (modal.open) {
      const target = $('review-error') || $('modal-error')
      target.textContent = error.message
      target.scrollIntoView({block:'nearest'})
    } else notify(error.message)
  }
  finally { controls.forEach(control => { control.disabled = false }) }
}
function receipt(result, message) {
  openModal('Recebimento registrado', `<div class="stack"><p class="note"><strong>${h(result.codigo)}</strong><br>Documento na fila de conferência.</p>
   ${message ? `<p class="hint">${h(message)}</p>` : ''}<button id="finish" type="button">Voltar ao painel</button></div>`)
  $('finish').onclick = () => modal.close()
}
function newReceipt() {
  openModal('Novo recebimento', `<div class="stack"><form id="upload" class="stack">
    <label>Tipo de documento<select name="tipo"><option value="">Identificar na leitura</option>${Object.entries(types).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></label>
    <div class="drop"><label>Foto, PDF ou imagem do QR Code<input type="file" name="arquivo" accept=".pdf,.png,.jpg,.jpeg"></label></div>
    <p id="selected-file" class="note" role="status">Nenhum arquivo selecionado.</p>
    <div class="camera-area"><button id="open-camera" type="button">Abrir câmera</button>
    <div id="camera-preview" hidden><video id="camera-video" autoplay playsinline muted></video>
    <div class="actions"><button id="take-photo" type="button">Fotografar documento</button><button id="stop-camera" type="button">Fechar câmera</button></div></div>
    <p id="camera-status" class="hint" role="status"></p></div>
    <p class="hint">PDF, PNG ou JPEG de até 12 MB. O documento recebe um protocolo e os campos legíveis vão para conferência.</p>
    <div class="actions"><button class="primary" type="submit">Protocolar e ler documento</button>
    <button id="manual" type="button">Registrar manualmente</button></div>
    <p id="upload-state" role="status" class="hint"></p></form></div>`)
  $('manual').onclick = manualReceipt
  const form = $('upload')
  let photo = null
  let selectionVersion = 0
  form.arquivo.addEventListener('change', () => {
    selectionVersion++
    photo = null
    $('camera-status').textContent = ''
    const file = form.arquivo.files[0]
    $('selected-file').textContent = file ? `Arquivo selecionado: ${file.name} (${Math.ceil(file.size / 1024)} KB)` : 'Nenhum arquivo selecionado.'
  })
  $('open-camera').onclick = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      $('camera-status').textContent = 'A câmera não está disponível neste navegador. Abra o sistema no Chrome ou Edge.'
      return
    }
    try {
      stopCamera()
      const stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}, audio:false})
      if (!modal.open || $('upload') !== form) { stream.getTracks().forEach(track => track.stop()); return }
      cameraStream = stream
      $('camera-video').srcObject = stream
      $('camera-preview').hidden = false
      $('camera-status').textContent = 'Posicione o documento diante da câmera.'
    } catch (error) {
      $('camera-status').textContent = 'Não foi possível abrir a câmera. Verifique a permissão do navegador e se há uma câmera conectada.'
    }
  }
  $('stop-camera').onclick = () => { stopCamera(); $('camera-preview').hidden = true }
  $('take-photo').onclick = () => {
    const video = $('camera-video')
    if (!video.videoWidth || !video.videoHeight) { $('camera-status').textContent = 'Aguarde a imagem da câmera aparecer.'; return }
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth; canvas.height = video.videoHeight
    canvas.getContext('2d').drawImage(video, 0, 0)
    const captureVersion = ++selectionVersion
    canvas.toBlob(blob => {
      if (!blob || !modal.open || $('upload') !== form || captureVersion !== selectionVersion) return
      photo = new File([blob], 'foto-documento.jpg', {type:'image/jpeg'})
      form.arquivo.value = ''
      $('selected-file').textContent = `Foto capturada: ${photo.name} (${Math.ceil(photo.size / 1024)} KB)`
      stopCamera(); $('camera-preview').hidden = true
      $('camera-status').textContent = 'Foto capturada. Clique em Protocolar e ler documento.'
    }, 'image/jpeg', 0.92)
  }
  form.onsubmit = event => {
    event.preventDefault()
    action(event.submitter, async () => {
      const file = photo || form.arquivo.files[0]
      if (!file) throw new Error('Selecione um arquivo ou fotografe o documento.')
      if (file.size > 12 * 1024 * 1024) throw new Error('O limite é de 12 MB.')
      $('upload-state').textContent = 'Enviando documento. A leitura pode levar alguns instantes.'
      try {
        const result = await api('/api/upload', {method:'POST',body:file,headers:{'X-P33-Tipo':form.elements.tipo.value}})
        const message = leituraMensagem(result)
        if (supervisor()) await detail(result.id, message || 'Documento recebido.')
        else receipt(result, message)
        refresh().catch(error => notify(error.message))
      } finally {
        if ($('upload-state')) $('upload-state').textContent = ''
      }
    })
  }
}
function manualReceipt() {
  openModal('Recebimento manual', `<form class="stack" id="record">
    <p class="hint">Informe o tipo e pelo menos um identificador do colaborador.</p>
    <div class="form-grid">${fields()}</div>
    <div class="actions"><button class="primary">Gerar protocolo</button></div></form>`)
  configurarCampos($('record'))
  $('record').onsubmit = event => {
    event.preventDefault()
    action(event.submitter, async () => {
      const result = await send('/api/protocolos', formObject(event.target))
      if (supervisor()) await detail(result.id)
      else receipt(result)
      refresh().catch(error => notify(error.message))
    })
  }
}
async function detail(id, message = '') {
  const row = await api('/api/protocolos/' + id)
  const drafts = row.status === 'recebido'
  const linhasPonto = lerLista(row.ocorrencias_ponto)
  const tratativas = lerLista(row.tratativas_ponto)
  const missing = Object.entries({data_inicio:'início do afastamento', data_fim:'fim do afastamento', cid:'CID', medico:'médico(a)', local_atestado:'local do atendimento', token_atestado:'token'}).filter(([key]) => !row[key]).map(([,label]) => label)
  const stage = {
    recebido:['Conferir documento','Conferi os dados com o original.'],
    conferido:['Registrar encaminhamento','Confirmo que encaminhei este protocolo.'],
    encaminhado:['Confirmar lançamento','Confirmei o lançamento com o responsável.']
  }[row.status]
  const label = row.status === 'conferido' ? `<label>Forma de envio<select name="canal" id="canal-envio"><option value="email">E-mail</option><option value="whatsapp">WhatsApp</option></select></label>
    <label id="destino-label">E-mail<input name="destino" type="email" maxlength="100" placeholder="nome@empresa.com.br"></label>
    <div class="actions"><button id="enviar-email" type="button">Enviar e-mail com anexo</button><button id="abrir-mensagem" type="button">Abrir e-mail</button></div>
    <p class="hint">O envio direto usa a conta remetente configurada no sistema. Para WhatsApp ou envio pelo seu aplicativo, baixe e anexe o documento manualmente.</p>` :
    row.status === 'encaminhado' ? '<label>Referência do lançamento<input name="referencia" maxlength="100" placeholder="Ex.: número do chamado"></label>' : ''
  openModal(row.codigo, `<div class="stack">
    <div class="actions"><span class="badge ${h(row.status)}">${h(status[row.status])}</span>
    <span class="hint">Recebido em ${dateTime.format(new Date(row.criado))}</span></div>
    ${row.retencao_ate ? `<p class="hint">Revisão de retenção em 90 dias: ${h(dateTime.format(new Date(row.retencao_ate)))}. ${retencaoConfig.retencao_automatica ? 'Retenção automática ativada conforme escopo configurado.' : 'Exclusão automática ainda não está ativada.'}</p>` : ''}
    ${message ? `<p class="note">${h(message)}</p>` : ''}
    ${drafts && row.mime && row.tipo === 'atestado' && missing.length ? `<p class="hint">Ainda sem preenchimento: ${h(missing.join(', '))}. Confira se essas informações constam no documento e preencha-as quando necessário.</p>` : ''}
    ${row.mime ? `<div class="actions"><a href="/api/protocolos/${id}/arquivo">Baixar original</a>
      ${drafts ? '<button id="reread" type="button">Tentar leitura novamente</button>' : ''}</div>` : ''}
    <form id="review" class="stack">
    ${drafts ? `<p class="hint">Para conferir, preencha tipo, nome, matrícula, data e setor e marque a confirmação abaixo.</p><div class="form-grid">${fields(row)}</div>` :
      `<p class="note"><strong>${h(row.nome)}</strong> · ${h(row.matricula)}<br>${h(types[row.tipo])} · ${h(row.setor)} · ${h(row.data.split('-').reverse().join('/'))}
      ${row.data_inicio ? `<br>Início: ${h(row.data_inicio.split('-').reverse().join('/'))}` : ''}${row.data_fim ? ` · Fim: ${h(row.data_fim.split('-').reverse().join('/'))}` : ''}
      ${row.cid ? `<br>CID: ${h(row.cid)}` : ''}${row.medico ? `<br>Médico(a): ${h(row.medico)}` : ''}${row.local_atestado ? `<br>Local: ${h(row.local_atestado)}` : ''}${row.token_atestado ? `<br>Token: ${h(row.token_atestado)}` : ''}
      ${row.outro_profissional ? `<br>Outro profissional: ${h(row.outro_profissional)} ${h(row.matricula_outro)}` : ''}${row.data_troca ? `<br>Data do plantão: ${h(row.data_troca.split('-').reverse().join('/'))}` : ''}${row.hora_inicio || row.hora_fim ? `<br>Horário: ${h(row.hora_inicio)} às ${h(row.hora_fim)}` : ''}${row.detalhes ? `<br>Observações: ${h(row.detalhes)}` : ''}
      ${row.tipo === 'ponto' && linhasPonto.length ? `<br>Ocorrências: ${linhasPonto.map(item=>`${h((item.data || '').split('-').reverse().join('/'))}: ${h(item.ocorrencia)} — ${h(item.justificativa)}`).join('; ')}` : ''}
      ${row.tipo === 'ponto' && tratativas.length ? `<br>Tratativas: ${tratativas.map(key=>h(tratativasPonto[key] || key)).join(', ')}` : ''}
      ${row.tipo === 'ponto' && row.troca_ponto_de ? `<br>Troca de: ${h(row.troca_ponto_de.split('-').reverse().join('/'))}` : ''}${row.tipo === 'ponto' && row.troca_ponto_para ? ` para ${h(row.troca_ponto_para.split('-').reverse().join('/'))}` : ''}
      ${row.destino ? `<br>Destino: ${h(row.destino)}` : ''}${row.referencia ? `<br>Referência: ${h(row.referencia)}` : ''}</p>`}
    ${label}
    ${stage ? `<label class="check"><input type="checkbox" name="confirmado">${stage[1]}</label>
      <p id="review-error" class="error" role="alert"></p>
      <div class="actions">${drafts ? '<button name="acao" value="salvar">Salvar rascunho</button>' : ''}
      <button class="primary" name="acao" value="avancar">${stage[0]}</button></div>` : ''}
    </form><h2>Histórico</h2><ol class="audit">${row.eventos.map(e=>`<li>${h(e.evento)} · ${h(e.login)} · ${dateTime.format(new Date(e.instante))}</li>`).join('')}</ol>
    <div class="actions"><button id="delete-protocol" type="button">Excluir protocolo</button></div></div>`)
  if (drafts) configurarCampos($('review'))
  $('delete-protocol').onclick = event => {
    if (!confirm(`Excluir o protocolo ${row.codigo}? O documento e o histórico serão removidos do sistema. Será mantido um registro mínimo com número, operador e horário da exclusão.`)) return
    action(event.target, async () => {
      await send('/api/protocolos/' + id + '/excluir', {revisao:row.revisao})
      modal.close()
      await refresh()
      notify('Protocolo excluído.')
    })
  }
  const canal = $('canal-envio')
  const destino = $('review').elements.destino
  if (canal) canal.onchange = () => {
    const whatsapp = canal.value === 'whatsapp'
    destino.value = ''
    destino.type = whatsapp ? 'tel' : 'email'
    destino.placeholder = whatsapp ? 'Ex.: 5511999999999 (com DDI e DDD)' : 'nome@empresa.com.br'
    $('destino-label').firstChild.textContent = whatsapp ? 'Telefone do WhatsApp' : 'E-mail'
    $('abrir-mensagem').textContent = whatsapp ? 'Abrir WhatsApp' : 'Abrir e-mail'
    $('enviar-email').hidden = whatsapp
    $('review-error').textContent = ''
  }
  if ($('enviar-email')) $('enviar-email').onclick = event => {
    if (!validarDestino()) return
    const address = destino.value.trim()
    if (!confirm(`Enviar o documento ${row.codigo} para ${address}?`)) return
    action(event.target, async () => {
      const result = await send('/api/protocolos/' + id + '/enviar-email', {destinatario:address, revisao:row.revisao})
      $('review-error').textContent = ''
      notify(result.mensagem)
      await detail(id, result.mensagem)
      $('review').elements.destino.value = address
    })
  }
  function validarDestino() {
    if (!canal) return true
    const value = destino.value.trim()
    const valid = canal.value === 'email' ? value && destino.checkValidity() : /^\+?[\d\s().-]+$/.test(value) && /^\d{12,15}$/.test(value.replace(/\D/g, ''))
    if (!valid) {
      $('review-error').textContent = canal.value === 'email' ? 'Informe um e-mail válido.' : 'Informe o telefone com DDI e DDD, por exemplo 5511999999999.'
      destino.focus()
      return false
    }
    $('review-error').textContent = ''
    return true
  }
  if ($('abrir-mensagem')) $('abrir-mensagem').onclick = () => {
    if (!validarDestino()) return
    const subject = encodeURIComponent(`Encaminhamento de protocolo ${row.codigo}`)
    const body = encodeURIComponent(`Olá,\n\nSegue o documento referente ao protocolo ${row.codigo}.\n\n`)
    if (canal.value === 'whatsapp') {
      window.open(`https://wa.me/${destino.value.replace(/\D/g, '')}?text=${body}`, '_blank', 'noopener,noreferrer')
    } else {
      window.location.href = `mailto:${encodeURIComponent(destino.value.trim())}?subject=${subject}&body=${body}`
    }
  }
  if ($('reread')) $('reread').onclick = event => action(event.target, async () => {
    const result = await send('/api/protocolos/' + id + '/reler')
    await detail(id, leituraMensagem(result))
    refresh().catch(error => notify(error.message))
  })
  $('review').onsubmit = event => {
    event.preventDefault()
    if (!event.submitter) return
    if (canal && !validarDestino()) return
    action(event.submitter, async () => {
      const form = event.target
      await send('/api/protocolos/' + id, {...formObject(form), revisao:row.revisao,
        acao:event.submitter.value, confirmado:form.confirmado?.checked === true})
      await detail(id, 'Atualização registrada.')
      refresh().catch(error => notify(error.message))
    })
  }
}
$('status').insertAdjacentHTML('beforeend', Object.entries(status).map(([key,label])=>`<option value="${key}">${label}</option>`).join(''))
$('type').insertAdjacentHTML('beforeend', Object.entries(types).map(([key,label])=>`<option value="${key}">${label}</option>`).join(''))
$('search').oninput = $('status').onchange = $('type').onchange = $('filter-sector').onchange = $('filter-from').onchange = $('filter-to').onchange = draw
$('metrics').onclick = event => {
  const button = event.target.closest('[data-status]')
  if (button) {
    $('status').value = $('status').value === button.dataset.status ? '' : button.dataset.status
    draw()
  }
}
$('rows').onclick = event => {
  const button = event.target.closest('[data-id]')
  if (button) detail(button.dataset.id).catch(error => notify(error.message))
}
function limparFiltros() {
  for (const id of ['search','status','type','filter-sector','filter-from','filter-to']) $(id).value = ''
  draw()
}
$('overview').onclick = limparFiltros
$('clear-filters').onclick = limparFiltros
$('privacy').onclick = () => {
  const expired = rows.filter(row => row.retencao_ate && Date.parse(row.retencao_ate) <= Date.now()).length
  const description = retencaoConfig.retencao_automatica ? `Exclusão automática ativada para ${retencaoConfig.retencao_escopo === 'todos' ? 'todos os protocolos' : 'protocolos concluídos'}, após 90 dias do recebimento.` : 'A proposta é revisar os documentos após 90 dias do recebimento. A exclusão automática não está ativada.'
  openModal('Privacidade e retenção', `<div class="stack"><p class="note"><strong>Retenção.</strong> ${description}</p>
    <p>${expired} protocolo(s) na lista atingiram 90 dias. Revise as necessidades de conservação com o hospital antes de excluir.</p>
    <p>O sistema pode conter informações pessoais e dados de saúde. Acesse e compartilhe apenas o necessário para o trabalho autorizado.</p>
    <p>Ao excluir manualmente, o sistema remove o protocolo e o documento da base ativa e mantém número do protocolo, operador, horário e motivo da exclusão. Cópias, e-mails, exportações e backups precisam de tratamento próprio.</p>
    <p>Controlador, contato para solicitações, regras de retenção e exceções devem ser definidos pela instituição responsável.</p></div>`)
}
$('refresh').onclick = () => refresh().catch(error => notify(error.message))
async function exportarArquivo(formato, button) {
  if (!visibleIds.length) return
  if (visibleIds.length > 900) { notify('Aplique filtros para exportar até 900 protocolos por vez.'); return }
  button.disabled = true
  try {
    const response = await fetch('/api/exportar', {method:'POST', headers:{'Content-Type':'application/json','X-P33-Request':'1'}, body:JSON.stringify({formato,ids:visibleIds})})
    if (!response.ok) { const result = await response.json(); throw new Error(result.erro || 'Não foi possível exportar.') }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `protocolos-p33.${formato}`
    document.body.append(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { notify(error.message || 'Não foi possível exportar.') }
  finally { button.disabled = !visibleIds.length }
}
$('export-excel').onclick = event => exportarArquivo('xlsx', event.currentTarget)
$('export-pdf').onclick = event => exportarArquivo('pdf', event.currentTarget)
$('new').onclick = $('nav-new').onclick = newReceipt
$('close').onclick = () => modal.close()
$('logout').onclick = async () => {
  try { await send('/api/logout'); showLogin() } catch (error) { notify(error.message) }
}
async function signIn(credentials, button, path = '/api/login') {
  button.disabled = true
  $('login-error').textContent = ''
  try { await enter(await send(path, credentials)) }
  catch (error) { $('login-error').textContent = error.message }
  finally { button.disabled = false }
}
$('login-form').onsubmit = event => {
  event.preventDefault()
  signIn(formObject(event.target), event.submitter)
}
$('demo-login').onclick = event => signIn({},event.target,'/api/demo-login')
api('/api/config').then(config=>{ retencaoConfig = config; $('demo-login').hidden = $('demo').hidden = !config.demo; $('logout').hidden = config.sem_login }).catch(()=>{})
api('/api/me').then(user => { $('startup').hidden = true; return enter(user) }).catch(error => {
  if (error.status === 401) { $('startup').hidden = true; showLogin() }
  else { $('startup').textContent = 'Não foi possível carregar o painel. Atualize a página ou verifique se o servidor está em execução.' }
})
