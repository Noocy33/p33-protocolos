const $ = id => document.getElementById(id)
const status = {recebido:'A conferir', conferido:'Conferido', encaminhado:'Encaminhado', baixado:'Lançamento confirmado'}
const types = {atestado:'Atestado', ponto:'Ocorrência de ponto'}
const modal = $('modal')
let account, rows = [], timer
const dateTime = new Intl.DateTimeFormat('pt-BR', {dateStyle:'short',timeStyle:'short',timeZone:'America/Sao_Paulo'})
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
  $('employee-col').hidden = !supervisor()
  $('search').placeholder = supervisor() ? 'Protocolo, matrícula, nome ou setor' : 'Protocolo ou setor'
  await refresh()
}
async function refresh() {
  rows = await api('/api/protocolos')
  draw()
}
function draw() {
  const selected = $('status').value
  $('metrics').innerHTML = Object.entries(status).map(([key,label]) =>
    `<button class="metric" data-status="${key}" aria-pressed="${selected === key}"><span>${label}</span><strong>${rows.filter(row=>row.status===key).length}</strong></button>`).join('')
  const query = normalize($('search').value.trim())
  const type = $('type').value
  const filtered = rows.filter(r => (!selected || r.status === selected) && (!type || r.tipo === type) &&
    [r.codigo,r.nome,r.matricula,r.setor].some(value => normalize(value).includes(query)))
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
  $('modal-title').textContent = title
  $('modal-body').innerHTML = content
  $('modal-error').textContent = ''
  if (!modal.open) modal.showModal()
}
function fields(row = {}) {
  return `<label class="full">Tipo de documento<select name="tipo"><option value="">Selecione</option>
    ${Object.entries(types).map(([key,label])=>`<option value="${key}" ${row.tipo===key?'selected':''}>${label}</option>`).join('')}</select></label>
    <label class="full">Nome do colaborador<input name="nome" maxlength="120" value="${h(row.nome)}"></label>
    <label>Matrícula<input name="matricula" maxlength="30" value="${h(row.matricula)}"></label>
    <label>CPF, se constar<input name="cpf" maxlength="18" inputmode="numeric" value="${h(row.cpf)}"></label>
    <label>Setor<input name="setor" maxlength="80" value="${h(row.setor)}"></label>
    <label>Data do documento<input name="data" type="date" value="${h(row.data)}"></label>
    <label>Dias informados, se aplicável<input name="dias" inputmode="numeric" maxlength="3" value="${h(row.dias)}"></label>`
}
async function action(button, fn) {
  const controls = [...(button.form?.querySelectorAll('button') || [button])]
  $('modal-error').textContent = ''
  controls.forEach(control => { control.disabled = true })
  try { await fn() }
  catch (error) { if (modal.open) $('modal-error').textContent = error.message; else notify(error.message) }
  finally { controls.forEach(control => { control.disabled = false }) }
}
function receipt(result, message) {
  openModal('Recebimento registrado', `<div class="stack"><p class="note"><strong>${h(result.codigo)}</strong><br>Documento na fila de conferência.</p>
   ${message ? `<p class="hint">${h(message)}</p>` : ''}<button id="finish" type="button">Voltar ao painel</button></div>`)
  $('finish').onclick = () => modal.close()
}
function newReceipt() {
  openModal('Novo recebimento', `<div class="stack"><form id="upload" class="stack">
    <div class="drop"><label>Foto, PDF ou imagem do QR Code<input type="file" name="arquivo" accept=".pdf,.png,.jpg,.jpeg" required></label></div>
    <label>Tirar foto pelo celular<input id="camera" type="file" accept="image/jpeg,image/png" capture="environment"></label>
    <p class="hint">PDF, PNG ou JPEG de até 12 MB. O documento recebe um protocolo e os campos legíveis vão para conferência.</p>
    <div class="actions"><button class="primary" type="submit">Protocolar e ler documento</button>
    <button id="manual" type="button">Registrar manualmente</button></div>
    <p id="upload-state" role="status" class="hint"></p></form></div>`)
  $('manual').onclick = manualReceipt
  const form = $('upload')
  const camera = $('camera')
  form.arquivo.addEventListener('change', () => { camera.value = '' })
  camera.addEventListener('change', () => {
    if (camera.files.length) form.arquivo.files = camera.files
  })
  form.onsubmit = event => {
    event.preventDefault()
    action(event.submitter, async () => {
      const file = form.arquivo.files[0]
      if (file.size > 12 * 1024 * 1024) throw new Error('O limite é de 12 MB.')
      $('upload-state').textContent = 'Enviando documento. A leitura pode levar alguns instantes.'
      try {
        const result = await api('/api/upload', {method:'POST',body:file})
        const message = [...result.metodos, ...result.avisos].join(' · ')
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
  const stage = {
    recebido:['Conferir documento','Conferi os dados com o original.'],
    conferido:['Registrar encaminhamento','Confirmo que encaminhei este protocolo.'],
    encaminhado:['Confirmar lançamento','Confirmei o lançamento com o responsável.']
  }[row.status]
  const label = row.status === 'conferido' ? '<label>Destino do encaminhamento<input name="destino" maxlength="100" placeholder="Ex.: RH"></label>' :
    row.status === 'encaminhado' ? '<label>Referência do lançamento<input name="referencia" maxlength="100" placeholder="Ex.: número do chamado"></label>' : ''
  openModal(row.codigo, `<div class="stack">
    <div class="actions"><span class="badge ${h(row.status)}">${h(status[row.status])}</span>
    <span class="hint">Recebido em ${dateTime.format(new Date(row.criado))}</span></div>
    ${message ? `<p class="note">${h(message)}</p>` : ''}
    ${row.mime ? `<div class="actions"><a href="/api/protocolos/${id}/arquivo">Baixar original</a>
      ${drafts ? '<button id="reread" type="button">Tentar leitura novamente</button>' : ''}</div>` : ''}
    <form id="review" class="stack">
    ${drafts ? `<div class="form-grid">${fields(row)}</div>` :
      `<p class="note"><strong>${h(row.nome)}</strong> · ${h(row.matricula)}<br>${h(types[row.tipo])} · ${h(row.setor)} · ${h(row.data.split('-').reverse().join('/'))}
      ${row.destino ? `<br>Destino: ${h(row.destino)}` : ''}${row.referencia ? `<br>Referência: ${h(row.referencia)}` : ''}</p>`}
    ${label}
    ${stage ? `<label class="check"><input type="checkbox" name="confirmado">${stage[1]}</label>
      <div class="actions">${drafts ? '<button name="acao" value="salvar">Salvar rascunho</button>' : ''}
      <button class="primary" name="acao" value="avancar">${stage[0]}</button></div>` : ''}
    </form><h2>Histórico</h2><ol class="audit">${row.eventos.map(e=>`<li>${h(e.evento)} · ${h(e.login)} · ${dateTime.format(new Date(e.instante))}</li>`).join('')}</ol></div>`)
  if ($('reread')) $('reread').onclick = event => action(event.target, async () => {
    const result = await send('/api/protocolos/' + id + '/reler')
    await detail(id, [...result.metodos,...result.avisos].join(' · ') || 'Leitura concluída.')
    refresh().catch(error => notify(error.message))
  })
  $('review').onsubmit = event => {
    event.preventDefault()
    if (!event.submitter) return
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
$('search').oninput = $('status').onchange = $('type').onchange = draw
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
$('overview').onclick = () => { $('search').value = $('status').value = $('type').value = ''; draw() }
$('refresh').onclick = () => refresh().catch(error => notify(error.message))
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
api('/api/config').then(config=>{ $('demo-login').hidden = $('demo').hidden = !config.demo }).catch(()=>{})
api('/api/me').then(enter).catch(()=>{})
