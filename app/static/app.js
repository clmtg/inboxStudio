'use strict';
const $ = id => document.getElementById(id);
let accountList = [], currentAccount = null, accountEditing = null, switching = false;
let saved, draft, status = {}, filter = 'all', editing = null, dragId = null, busy = false;
const fields = {sender_domain: 'Sender domain', sender_email: 'Sender email', subject: 'Subject', body: 'Body', subject_or_body: 'Subject or body', age_hours: 'Received age', flagged: 'Flag status'};
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
const dirty = () => saved && JSON.stringify(saved) !== JSON.stringify(draft);
const error = message => { $('error').textContent = message; $('error').hidden = !message; };
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 3500); }
async function api(path, data, accountId=currentAccount) {
  if (['rules','status','scan'].includes(path)) path += '?account='+encodeURIComponent(accountId || 'default');
  const response = await fetch(location.origin + '/api/' + path, data === undefined ? {} : {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)});
  if (response.status === 401) throw new Error('Your session needs a sign-in. Reload this page.');
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The request failed');
  return result;
}
function conditionText(c) {
  if (c.field === 'flagged') return 'Message is flagged';
  if (c.field === 'sender_domain') return `From ${c.value} (+ subdomains)`;
  if (c.field === 'sender_email') return `Sender email ${c.op === 'is' ? 'is' : 'contains'} “${c.value}”`;
  if (c.field === 'age_hours') return `${c.op === 'older_than' ? 'Received over' : 'Received at most'} ${c.value}h ago`;
  return `${fields[c.field]} ${c.op === 'not_contains' ? 'excludes' : 'contains'} “${c.value}”`;
}
function outcomes(rule) { return rule.action === 'conditional' ? [...rule.branches, rule.otherwise] : [rule]; }
function needsFolder(action) { return action==='move'||action==='move_after'||action==='trash'; }
function actionText(action) { return action.action==='move_after' ? `Move to ${action.folder} after ${action.delay_hours}h` : needsFolder(action.action) ? (action.action==='trash'?'Move to Trash · ':'Move to ') + action.folder : {keep:'Keep in Inbox',delete:'Delete permanently',continue:'Continue to next rule'}[action.action]; }
function branchText(rule) { return rule.action === 'conditional' ? rule.branches.map((b,i)=>(i?'Else if ':'If ')+b.conditions.map(conditionText).join(' + ')+' → '+actionText(b)).concat('Otherwise → '+actionText(rule.otherwise)).join(' · ') : ''; }
function render() {
  if (!draft) return;
  const rules = draft.rules;
  $('nav-count').textContent = rules.length;
  $('all-count').textContent = rules.length;
  $('active-count').textContent = rules.filter(r => r.enabled).length;
  $('total-label').textContent = `of ${rules.length} rules enabled`;
  $('interval-label').textContent = `${Math.round(draft.settings.interval_seconds / 60)} min`;
  $('preview-mode').checked = draft.settings.preview;
  $('scan-interval').value = draft.settings.interval_seconds / 60;
  const preview = saved.settings.preview;
  $('mode-badge').textContent = preview ? '● Preview mode' : '● Live mode';
  $('mode-badge').classList.toggle('live', !preview);
  $('save-bar').hidden = !dirty();
  const q = $('search').value.toLocaleLowerCase();
  const visible = rules.filter(r => (filter === 'all' || outcomes(r).some(a=>a.action===filter || filter==='move'&&a.action==='move_after')) && (r.name + ' ' + (r.folder||'') + ' ' + outcomes(r).map(actionText).join(' ') + ' ' + r.conditions.map(conditionText).join(' ') + ' ' + branchText(r)).toLocaleLowerCase().includes(q));
  const currentResults = status.revision === saved.revision && !dirty() ? status.results || [] : [];
  $('rule-list').innerHTML = visible.map(rule => {
    const i = rules.indexOf(rule), count = currentResults.find(r => r.id === rule.id)?.count;
    const action = rule.action === 'conditional' ? 'If / Otherwise' : rule.action === 'move' ? rule.folder : actionText(rule);
    return `<article class="rule-card ${rule.enabled ? '' : 'disabled'}" data-id="${escapeHTML(rule.id)}"><div class="priority"><span class="drag-grip" draggable="true" title="Drag to reorder" aria-hidden="true">⠿</span>${String(i+1).padStart(2,'0')}</div><div class="rule-icon ${rule.action}" aria-hidden="true">${rule.action === 'move' || rule.action === 'move_after' ? '↳' : rule.action === 'keep' ? '◇' : rule.action === 'delete' ? '×' : '→'}</div><div><div class="rule-title">${escapeHTML(rule.name)}${count === undefined ? '' : `<span class="match-pill">${count} matched</span>`}</div><div class="rule-description">${rule.conditions.map(c => escapeHTML(conditionText(c))).join(' <span aria-hidden="true">·</span> ')}${rule.action==='conditional'?'<div class="branch-summary">'+escapeHTML(branchText(rule))+'</div>':''}</div></div><div class="rule-action ${rule.action}"><small>${rule.action === 'move' || rule.action === 'move_after' ? 'Move to' : 'Action'}</small>${escapeHTML(action)}</div><div class="card-controls"><div class="order-buttons"><button data-do="up" aria-label="Move ${escapeHTML(rule.name)} up" ${i===0?'disabled':''}>▲</button><button data-do="down" aria-label="Move ${escapeHTML(rule.name)} down" ${i===rules.length-1?'disabled':''}>▼</button></div><button class="toggle" role="switch" aria-label="Enable ${escapeHTML(rule.name)}" aria-checked="${rule.enabled}" data-do="toggle"></button><button class="icon-button" data-do="edit" aria-label="Edit ${escapeHTML(rule.name)}" title="Edit rule">✎</button><button class="icon-button" data-do="remove" aria-label="Remove ${escapeHTML(rule.name)}" title="Remove rule">×</button></div></article>`;
  }).join('') || '<div class="empty">' + (rules.length ? 'No rules match this filter.' : 'Your Inbox is a blank slate. Add your first rule.') + '</div>';
}
function renderStatus() {
  const total = (status.results || []).reduce((sum, item) => sum + item.count, 0);
  $('match-count').textContent = status.finished_at ? total : '—';
  const stale = status.revision !== saved?.revision;
  const paused = currentAccount && !accountList.find(a=>a.id===currentAccount)?.enabled;
  const offline = !paused && (status.state === 'running' ? Date.now()/1000 - (status.heartbeat_at || status.started_at || 0) > 20 : status.next_scan_at && Date.now()/1000 > status.next_scan_at + 30);
  $('match-label').textContent = offline ? 'Worker may be offline' : status.state === 'running' ? 'Scan in progress' : stale && status.finished_at ? 'Previous rule version' : status.preview ? 'preview · no mail changed' : status.finished_at ? 'across your rules' : 'No scan yet';
  const names = {running:'Scanning your Inbox…',success:'Scan completed',error:'Scan needs attention',waiting:'Waiting for the worker'};
  const when = status.finished_at || status.started_at;
  $('activity-summary').innerHTML = `<p class="activity-state ${status.state === 'error' ? 'activity-error' : ''}">${paused ? 'Account paused' : offline ? 'Worker may be offline' : names[status.state] || 'Waiting for the worker'}</p><p class="muted">${when ? escapeHTML(new Date(when*1000).toLocaleString()) : 'Start the IMAPFilter service to see results.'}${status.revision !== undefined ? ` · Rules revision ${status.revision} · ${status.preview ? 'Preview' : 'Live'}` : ''}</p>${status.inbox_count === undefined ? '' : `<p class="muted">${status.inbox_count} Inbox messages checked. ${total} matches reported${status.state === 'error' ? ' before the error' : ''}.</p>`}${stale && status.finished_at ? '<p class="muted">Saved rules have changed since this scan.</p>' : ''}`;
  $('results-list').innerHTML = (status.results || []).map(result => `<div class="result-row"><span>${escapeHTML(saved?.rules.find(r=>r.id===result.id)?.name || result.id)}</span><b>${result.count} matched</b></div>`).join('') || '<p class="muted">Results will appear after the worker evaluates your rules.</p>';
  $('worker-log').textContent = (status.logs || []).join('\n') || 'No log entries yet.';
  $('folders').innerHTML = (status.folders || []).map(f => `<option value="${escapeHTML(f)}"></option>`).join('');
  for (const id of ['scan-now','activity-scan']) { $(id).disabled = !currentAccount || switching || !accountList.find(a=>a.id===currentAccount)?.enabled || status.state === 'running' && !offline; $(id).textContent = status.state === 'running' && !offline ? '↻ Scanning…' : '↻ Scan now'; }
}
function changeOrder(id, target) {
  const source = draft.rules.findIndex(r=>r.id===id);
  if (source < 0 || target < 0 || target >= draft.rules.length) return;
  const [rule] = draft.rules.splice(source,1); draft.rules.splice(target,0,rule); render();
}
$('rule-list').addEventListener('click', event => {
  const button = event.target.closest('button[data-do]'), row = button?.closest('.rule-card');
  if (!row || busy) return;
  const index = draft.rules.findIndex(r=>r.id===row.dataset.id), rule = draft.rules[index];
  if (button.dataset.do === 'edit') return editRule(rule);
  if (button.dataset.do === 'toggle') rule.enabled = !rule.enabled;
  if (button.dataset.do === 'remove') { if (!confirm(`Remove “${rule.name}”? This removes the rule, not any emails.`)) return; draft.rules.splice(index,1); }
  if (button.dataset.do === 'up') return changeOrder(rule.id,index-1);
  if (button.dataset.do === 'down') return changeOrder(rule.id,index+1);
  render();
});
$('rule-list').addEventListener('dragstart', e=> { const row=e.target.closest('.rule-card'); if (!row || busy) return; dragId=row.dataset.id; e.dataTransfer.setData('text/plain',dragId); e.dataTransfer.effectAllowed='move'; row.classList.add('dragging'); });
$('rule-list').addEventListener('dragover', e=> { if (!dragId) return; e.preventDefault(); document.querySelectorAll('.drag-over').forEach(el=>el.classList.remove('drag-over')); e.target.closest('.rule-card')?.classList.add('drag-over'); });
$('rule-list').addEventListener('drop', e=> { e.preventDefault(); const row=e.target.closest('.rule-card'); if (row && dragId) changeOrder(dragId,draft.rules.findIndex(r=>r.id===row.dataset.id)); dragId=null; });
$('rule-list').addEventListener('dragend', ()=>{dragId=null; render();});
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter; document.querySelectorAll('[data-filter]').forEach(t=>t.classList.toggle('selected',t===b));render();}));
$('search').addEventListener('input',render);
function showPage(page) { document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!=='page-'+page);document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.page===page));$('breadcrumb').textContent={rules:'Mail rules',activity:'Scan activity',settings:'Settings',accounts:'Accounts'}[page]; }
document.querySelectorAll('[data-page]').forEach(button=>button.addEventListener('click',()=>showPage(currentAccount?button.dataset.page:'accounts')));

function operators(field) { return field==='flagged' ? [['is','is flagged']] : field==='sender_domain' ? [['is','is domain']] : field==='sender_email' ? [['is','is'],['contains','contains']] : field==='age_hours' ? [['older_than','more than'],['at_most','at most']] : [['contains','contains'],['not_contains','does not contain']]; }
function addCondition(condition={field:'sender_domain',op:'is',value:''}, target=$('conditions')) {
  const row=document.createElement('div');row.className='condition';
  row.innerHTML=`<select class="condition-field" aria-label="Condition field">${Object.entries(fields).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><select class="condition-op" aria-label="Comparison"></select><input class="condition-value" aria-label="Condition value" required maxlength="300"><button type="button" class="icon-button remove-condition" aria-label="Remove condition">×</button>`;
  row.querySelector('.condition-field').value=condition.field;
  function sync(reset=false) { const f=row.querySelector('.condition-field').value;const input=row.querySelector('input'); row.querySelector('.condition-op').innerHTML=operators(f).map(([k,v])=>`<option value="${k}">${v}</option>`).join('');input.type=f==='age_hours'?'number':'text';input.hidden=f==='flagged';input.required=f!=='flagged';input.placeholder=f==='age_hours'?'Hours, e.g. 72':f==='sender_domain'?'e.g. amazon.fr':f==='sender_email'?'e.g. notifications-noreply@linkedin.com':'Text to match';if(f==='age_hours'){input.min='0.01';input.max='87600';input.step='any';}else{input.removeAttribute('min');input.removeAttribute('max');input.removeAttribute('step');}if(reset)input.value=''; }
  sync();row.querySelector('.condition-op').value=condition.op;row.querySelector('input').value=condition.value;
  row.querySelector('.condition-field').addEventListener('change',()=>sync(true));
  row.querySelector('.remove-condition').addEventListener('click',()=>{if(target.children.length===1){toast('Every rule needs at least one condition.');return;}row.remove();});
  target.append(row);
}
function actionHelp() {
  const action=$('rule-action').value;
  $('folder-field').hidden=!needsFolder(action);$('rule-folder').required=needsFolder(action);
  $('delay-field').hidden=action!=='move_after';$('rule-delay').required=action==='move_after';
  $('conditional-editor').hidden=action!=='conditional';
  $('conditional-editor').querySelectorAll('input,select,button').forEach(el=>el.disabled=action!=='conditional');
  $('action-help').classList.toggle('danger',action==='delete');
  $('action-help').textContent=action==='conditional'?'Branches can move, move after a delay, trash, keep, delete, or continue. Keep stops later rules; continue allows them to act.':action==='move_after'?'Matching email stays in Inbox until it is older than the delay, then moves on the next scan.':action==='continue'?'Leave the message unchanged here and evaluate the next rule.':action==='delete'?'Matching emails are permanently deleted in live mode. They are not moved to Trash.':action==='trash'?'Choose this account’s Trash folder (for iCloud, usually Deleted Messages). Mail stays there until you or your provider empties it.':action==='keep'?'Leave matching messages in Inbox and stop evaluating later rules.':'Use an existing folder for this account, such as Store/Amazon.';
}
function actionEditor(target, action={action:'keep',folder:''}) {
  target.innerHTML='<label class="field">Action<select class="branch-kind"><option value="move">Move to a folder</option><option value="move_after">Move after a delay</option><option value="keep">Keep in Inbox</option><option value="trash">Move to Trash</option><option value="delete">Delete permanently</option><option value="continue">Continue to next rule</option></select></label><label class="field branch-folder-label">Destination folder<input class="branch-folder" list="folders" maxlength="255" placeholder="Store/Amazon"></label><label class="field branch-delay-label">Delay in hours<input class="branch-delay" type="number" min="0.01" max="87600" step="any" value="5"></label><p class="branch-action-help action-help"></p>';
  const kind=target.querySelector('select'),folder=target.querySelector('.branch-folder'),delay=target.querySelector('.branch-delay');kind.value=action.action;folder.value=action.folder||'';delay.value=action.delay_hours||5;
  const sync=()=>{target.querySelector('.branch-folder-label').hidden=!needsFolder(kind.value);folder.required=needsFolder(kind.value);target.querySelector('.branch-delay-label').hidden=kind.value!=='move_after';delay.required=kind.value==='move_after';folder.placeholder=kind.value==='trash'?'Trash folder, e.g. Deleted Messages':'Store/Amazon';target.querySelector('.branch-action-help').textContent=kind.value==='move_after'?'Moves only after the message is older than this delay.':kind.value==='trash'?'Choose the account’s Trash folder. Mail stays there until you or your provider empties it.':kind.value==='delete'?'Permanently deletes matching mail in live mode.':'';};kind.addEventListener('change',sync);sync();
}
function addBranch(branch={conditions:[{field:'age_hours',op:'older_than',value:24}],action:'move',folder:''}) {
  const card=document.createElement('section');card.className='branch-card';
  card.innerHTML='<div class="section-heading"><h3>If all match</h3><div><button type="button" class="text-button branch-up" aria-label="Move branch up">↑</button><button type="button" class="text-button branch-down" aria-label="Move branch down">↓</button><button type="button" class="text-button branch-remove">Remove branch</button></div></div><div class="branch-conditions"></div><button type="button" class="text-button branch-add">＋ Add condition</button><h3 class="then-label">Then</h3><div class="branch-action"></div>';
  const conditions=card.querySelector('.branch-conditions');branch.conditions.forEach(c=>addCondition(c,conditions));actionEditor(card.querySelector('.branch-action'),branch);
  card.querySelector('.branch-add').onclick=()=>{if(conditions.children.length<10)addCondition(undefined,conditions);};
  card.querySelector('.branch-remove').onclick=()=>{if($('branches').children.length>1)card.remove();else toast('Keep at least one branch.');};
  card.querySelector('.branch-up').onclick=()=>{if(card.previousElementSibling)card.previousElementSibling.before(card);};
  card.querySelector('.branch-down').onclick=()=>{if(card.nextElementSibling)card.nextElementSibling.after(card);};
  $('branches').append(card);
}
function readConditions(target) { return [...target.children].map(row=>{const field=row.querySelector('.condition-field').value;return {field,op:row.querySelector('.condition-op').value,value:field==='flagged'?true:field==='age_hours'?Number(row.querySelector('input').value):row.querySelector('input').value.trim()};}); }
function readAction(target) { const action=target.querySelector('select').value,result={action,folder:needsFolder(action)?target.querySelector('.branch-folder').value.trim():''};if(action==='move_after')result.delay_hours=Number(target.querySelector('.branch-delay').value);return result; }
$('add-branch').onclick=()=>{if($('branches').children.length<10)addBranch();else toast('Use at most ten branches.');};
function editRule(rule) {
  editing=rule?.id || null;$('dialog-title').textContent=rule?'Edit rule':'New rule';$('rule-name').value=rule?.name || '';$('rule-action').value=rule?.action || 'move';$('rule-folder').value=rule?.folder || '';$('rule-delay').value=rule?.delay_hours||5;$('rule-enabled').checked=rule?.enabled ?? true;$('conditions').replaceChildren();(rule?.conditions || [{field:'sender_domain',op:'is',value:''}]).forEach(c=>addCondition(c));$('branches').replaceChildren();(rule?.action==='conditional'?rule.branches:[{conditions:[{field:'age_hours',op:'older_than',value:24}],action:'move',folder:''}]).forEach(b=>addBranch(b));actionEditor($('otherwise-editor').querySelector('.branch-action'),rule?.otherwise||{action:'keep',folder:''});$('form-error').hidden=true;actionHelp();$('rule-dialog').showModal();$('rule-name').focus();
}
$('add-rule').addEventListener('click',()=>{if(draft&&!busy)editRule();});
$('add-condition').addEventListener('click',()=>{if($('conditions').children.length>=10)return toast('Use at most ten conditions.');addCondition();});
$('rule-action').addEventListener('change',actionHelp);
for(const id of ['close-dialog','cancel-dialog'])$(id).addEventListener('click',()=>$('rule-dialog').close());
$('rule-form').addEventListener('submit',event=>{
  event.preventDefault();
  const conditions=readConditions($('conditions'));
  if(conditions.some(c=>c.value==='' || c.field==='sender_domain' && !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/i.test(c.value))){$('form-error').textContent='Use a valid domain without @, and fill in every condition.';$('form-error').hidden=false;return;}
  const rule={id:editing || (globalThis.crypto?.randomUUID?.() || `rule-${Date.now()}-${Math.random().toString(36).slice(2)}`),name:$('rule-name').value.trim(),enabled:$('rule-enabled').checked,action:$('rule-action').value,folder:needsFolder($('rule-action').value)?$('rule-folder').value.trim():'',conditions};if(rule.action==='move_after')rule.delay_hours=Number($('rule-delay').value);
  if(rule.action==='conditional'){rule.branches=[...$('branches').children].map(card=>({...readAction(card.querySelector('.branch-action')),conditions:readConditions(card.querySelector('.branch-conditions'))}));rule.otherwise=readAction($('otherwise-editor').querySelector('.branch-action'));}
  const allConditions=conditions.concat(rule.branches?.flatMap(b=>b.conditions)||[]);
  if(allConditions.some(c=>c.field==='sender_domain'&&!/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/i.test(c.value))){$('form-error').textContent='Use a valid sender domain without @ in every branch.';$('form-error').hidden=false;return;}
  if(outcomes(rule).some(a=>a.action==='move_after'&&(!Number.isFinite(a.delay_hours)||a.delay_hours<=0||a.delay_hours>87600))){$('form-error').textContent='Use a move delay greater than 0 and at most 87600 hours.';$('form-error').hidden=false;return;}
  if(!rule.name || needsFolder(rule.action)&&!rule.folder){$('form-error').textContent='Enter a name and destination folder.';$('form-error').hidden=false;return;}
  if(editing)draft.rules[draft.rules.findIndex(r=>r.id===editing)]=rule;else draft.rules.push(rule);
  $('rule-dialog').close();render();toast('Rule updated in your draft. Save to apply.');
});
$('preview-mode').addEventListener('change',()=>{if(!draft||busy)return;draft.settings.preview=$('preview-mode').checked;render();});
$('scan-interval').addEventListener('change',()=>{if(!draft||busy)return;const minutes=Number($('scan-interval').value);if(!Number.isInteger(minutes)||minutes<1||minutes>1440){toast('Choose 1–1440 whole minutes.');return render();}draft.settings.interval_seconds=minutes*60;render();});
$('discard').addEventListener('click',()=>{if(busy)return;if(confirm('Discard your unsaved changes?')){draft=clone(saved);error('');render();}});
$('save').addEventListener('click',async()=>{
  if(busy||!dirty())return;
  if(saved.settings.preview&&!draft.settings.preview&&!confirm('Enable live mode? Matching mail will be moved or permanently deleted on the next scan.'))return;
  if(!draft.settings.preview&&JSON.stringify(saved.rules)!==JSON.stringify(draft.rules)&&draft.rules.some(r=>r.enabled&&outcomes(r).some(a=>a.action==='delete'))&&!confirm('Save these rules in live mode? Enabled delete rules permanently delete matching messages.'))return;
  busy=true;$('save').disabled=true;error('');
  const submitted=clone(draft);
  try{const result=await api('rules',submitted);saved=result;if(JSON.stringify(draft)===JSON.stringify(submitted))draft=clone(result);else draft.revision=result.revision;render();toast('Changes saved. They apply on the next scan.');}catch(e){error(e.message);}finally{busy=false;$('save').disabled=false;}
});
async function scanNow(){if(!currentAccount||switching)return;if(dirty()){toast('Save or discard your changes before scanning.');return;}try{await api('scan',{});toast('Scan queued. The worker will start it shortly.');}catch(e){error(e.message);}}
$('scan-now').addEventListener('click',scanNow);$('activity-scan').addEventListener('click',scanNow);
$('export-rules').addEventListener('click',()=>{if(!saved)return;const url=URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='inbox-rules-'+currentAccount+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
function renderAccounts() {
  $('account-select').innerHTML=accountList.map(a=>`<option value="${escapeHTML(a.id)}">${escapeHTML(a.name)}${a.enabled?'':' (paused)'}</option>`).join('') || '<option value="">Add an account</option>';
  $('account-select').value=currentAccount||'';
  $('account-select').disabled=!accountList.length||switching;
  $('account-list').innerHTML=accountList.map(a=>`<article class="panel account-card"><div><h2>${escapeHTML(a.name)}</h2><p>${escapeHTML(a.username)}</p><p class="muted">${escapeHTML(a.host)} · ${a.enabled?'Scanning enabled':'Paused'}${a.id===currentAccount?' · Selected':''}</p></div><div><button class="secondary" data-account-use="${escapeHTML(a.id)}">View rules</button> <button class="secondary" data-account-edit="${escapeHTML(a.id)}">Edit connection</button></div></article>`).join('') || '<div class="panel empty"><h2>Connect your first mailbox</h2><p>Add an account above, test the connection, then create your first rule.</p></div>';
  $('add-rule').disabled=!currentAccount||switching;
  $('preview-mode').disabled=!currentAccount||switching;
  $('scan-interval').disabled=!currentAccount||switching;
  $('export-rules').disabled=!currentAccount||switching;
}
async function loadAccount(id) {
  if(busy||switching)return;
  if(dirty()){toast('Save or discard your rule changes before switching accounts.');$('account-select').value=currentAccount;return;}
  switching=true;busy=true;renderAccounts();
  try {
    const [document, activity]=await Promise.all([api('rules',undefined,id),api('status',undefined,id)]);
    currentAccount=id;saved=document;draft=clone(saved);status=activity;error('');
    render();renderStatus();
  } catch(e){error(e.message);} finally{switching=false;busy=false;renderAccounts();renderStatus();}
}
$('account-select').addEventListener('change',()=>loadAccount($('account-select').value));
$('account-list').addEventListener('click',async event=>{
  const edit=event.target.closest('[data-account-edit]'), use=event.target.closest('[data-account-use]');
  if(edit)editAccount(accountList.find(a=>a.id===edit.dataset.accountEdit));
  if(use){await loadAccount(use.dataset.accountUse);if(currentAccount===use.dataset.accountUse)showPage('rules');}
});
function editAccount(account) {
  if(busy||switching)return;
  accountEditing=account||null;
  $('account-dialog-title').textContent=account?'Edit account':'Add account';
  $('account-name').value=account?.name||'';
  $('account-host').value=account?.host||'imap.mail.me.com';
  $('account-port').value=account?.port||993;
  $('account-provider').value=$('account-host').value==='imap.mail.me.com'?'icloud':'custom';
  $('account-username').value=account?.username||'';
  $('account-password').value='';$('account-password').required=!account;
  $('password-hint').textContent=account?'Leave blank to keep the saved password. Re-enter it if changing the server, port, or username.':'Use your provider’s app-specific password. OAuth-only accounts are not supported.';
  $('account-enabled').checked=account?.enabled??true;$('account-feedback').hidden=true;
  $('account-dialog').showModal();$('account-name').focus();
}
$('add-account').onclick=()=>editAccount();
$('close-account').onclick=()=>$('account-dialog').close();
$('account-dialog').addEventListener('close',()=>{$('account-password').value='';accountEditing=null;});
$('account-provider').onchange=()=>{if($('account-provider').value==='icloud'){$('account-host').value='imap.mail.me.com';$('account-port').value=993;}else{$('account-host').value='';$('account-host').focus();}};
function accountPayload() {
  return {id:accountEditing?.id,revision:accountEditing?.revision,name:$('account-name').value.trim(),host:$('account-host').value.trim(),port:Number($('account-port').value),username:$('account-username').value.trim(),password:$('account-password').value,enabled:$('account-enabled').checked};
}
async function submitAccount(test) {
  if(!$('account-form').reportValidity())return;
  const payload=accountPayload();
  $('account-form').querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
  const feedback=$('account-feedback');feedback.hidden=false;feedback.textContent=test?'Testing connection…':'Saving account…';
  try {
    const result=await api(test?'accounts/test':'accounts',payload);
    if(test){feedback.textContent=result.message;feedback.classList.toggle('danger',!result.ok);}
    else {
      accountList=(await api('accounts')).accounts;renderAccounts();renderStatus();
      $('account-dialog').close();toast('Account saved. Changes apply to future scans.');
      if(!currentAccount){await loadAccount(result.id);showPage('rules');}
    }
  }catch(e){feedback.textContent=e.message;feedback.classList.add('danger');}
  finally{$('account-form').querySelectorAll('input,select,button').forEach(el=>el.disabled=false);}
}
$('account-form').addEventListener('submit',event=>{event.preventDefault();submitAccount(false);});
$('test-account').onclick=()=>submitAccount(true);
async function poll(){
  const id=currentAccount;
  try{
    if(id&&!switching){const next=await api('status',undefined,id);if(currentAccount===id&&!switching){status=next;renderStatus();if(!dragId&&!['INPUT','SELECT'].includes(document.activeElement?.tagName)&&!$('rule-dialog').open)render();}}
    if(!id){accountList=(await api('accounts')).accounts;renderAccounts();if(accountList.length)await loadAccount(accountList[0].id);}
  }catch(e){error(e.message);}finally{setTimeout(poll,5000);}
}
(async()=>{try{accountList=(await api('accounts')).accounts;renderAccounts();if(accountList.length)await loadAccount(accountList[0].id);else{$('mode-badge').textContent='No account';showPage('accounts');}poll();}catch(e){error(e.message);}})();

// Keep mobile dialogs inside the visible area while the keyboard is open.
function updateDialogViewport() {
  const viewport = window.visualViewport;
  document.documentElement.style.setProperty('--visual-height', `${viewport?.height || window.innerHeight}px`);
  document.documentElement.style.setProperty('--visual-top', `${viewport?.offsetTop || 0}px`);
}
window.visualViewport?.addEventListener('resize', updateDialogViewport);
window.visualViewport?.addEventListener('scroll', updateDialogViewport);
window.addEventListener('resize', updateDialogViewport);
updateDialogViewport();
