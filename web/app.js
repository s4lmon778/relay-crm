'use strict';
let workspace, report, currentView='pipeline';
const byId=id=>document.getElementById(id);
const money=cents=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:cents%100?2:0,maximumFractionDigits:cents%100?2:0}).format(cents/100);
const cents=value=>{if(!/^\d+(\.\d{1,2})?$/.test(value))throw new Error('Use a non-negative amount with at most two decimal places.');const [whole,fraction='']=value.split('.');const total=Number(whole)*100+Number(fraction.padEnd(2,'0'));if(!Number.isSafeInteger(total))throw new Error('Amount is too large.');return total;};
const node=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
async function request(path,payload,key=crypto.randomUUID()){
  const options=payload?{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key},body:JSON.stringify(payload)}:{};
  const response=await fetch('/api/'+path,options);const body=await response.json();
  if(!response.ok)throw new Error(body.error||'Request failed');return body;
}
function errorView(target,error){target.replaceChildren(node('p',error.message,'error'));}
async function refresh(){
  [workspace,report]=await Promise.all([request('workspace'),request('report')]);
  byId('open-value').textContent=money(report.open_cents);byId('weighted-value').textContent=money(report.weighted_cents);
  byId('won-value').textContent=money(report.won_cents);byId('overdue-value').textContent=report.overdue;
  renderBoard();renderAccounts();renderReport();
}
function accountName(id){return workspace.accounts.find(a=>a.id===id)?.name||'Unknown account';}
function renderBoard(){
  const board=byId('board');board.replaceChildren();const search=byId('search').value.trim().toLowerCase();
  for(const [name,stages] of [['Lead',['Lead']],['Qualified',['Qualified']],['Proposal',['Proposal']],['Closed',['Won','Lost']]]){
    const rows=workspace.opportunities.filter(o=>stages.includes(o.stage)&&(`${o.title} ${accountName(o.account_id)}`).toLowerCase().includes(search));
    const column=node('section','','column');const head=node('div','','column-head');const label=node('span',name);label.append(node('span',rows.length,'count'));head.append(label,node('span',money(rows.reduce((s,o)=>s+o.amount_cents,0)),'column-total'));column.append(head);
    for(const o of rows){
      const card=node('button','','card');card.setAttribute('aria-label',`${o.title}, ${accountName(o.account_id)}, ${o.stage}`);
      card.append(node('div',accountName(o.account_id),'card-account'),node('div',o.title,'card-title'),node('div',money(o.amount_cents),'card-value'));
      const bottom=node('div',o.next_action,'card-bottom');const late=o.due_date<new Date().toLocaleDateString('en-CA')&&!['Won','Lost'].includes(o.stage);
      bottom.append(node('span',`${o.stage==='Won'?'Won · ':o.stage==='Lost'?'Lost · ':''}${late?'Overdue · ':''}${o.due_date}`,'due'+(late?' overdue':'')));card.append(bottom);card.onclick=()=>showOpportunity(o.id);column.append(card);
    }
    if(!rows.length)column.append(node('div',search?'No matching opportunities.':'No opportunities in this stage.','empty'));
    board.append(column);
  }
}
function renderAccounts(){
  const list=byId('account-list');list.replaceChildren();
  for(const a of workspace.accounts){
    const box=node('article','','account');box.append(node('h3',a.name),node('p',a.domain,'muted'));
    for(const c of workspace.contacts.filter(c=>c.account_id===a.id)){const row=node('div','','contact-row');row.append(node('strong',c.name),node('span',c.email));box.append(row);}
    const stats=report.accounts.find(r=>r.id===a.id);box.append(node('p',`${stats.opportunities} opportunities · ${money(stats.open_cents)} open pipeline`,'muted'));list.append(box);
  }
}
function renderReport(){
  const list=byId('stage-report');list.replaceChildren();const max=Math.max(1,...report.stages.map(r=>r.value_cents));
  for(const s of ['Lead','Qualified','Proposal','Won','Lost']){
    const row=report.stages.find(r=>r.stage===s)||{count:0,value_cents:0};const line=node('div','','report-row');
    const track=node('div','','bar-track'),fill=node('div','','bar-fill');fill.style.width=`${100*row.value_cents/max}%`;track.append(fill);
    line.append(node('span',`${s} (${row.count})`),track,node('strong',money(row.value_cents)));list.append(line);
  }
  byId('coverage').replaceChildren(...report.accounts.map(a=>{const row=node('div','','coverage-row');row.append(node('span',a.name),node('span',`${a.opportunities} opportunities · ${money(a.open_cents)} open`));return row;}));
}
function field(label,name,type='text',value=''){
  const wrap=node('label',label,'field');const input=document.createElement(type==='textarea'?'textarea':'input');
  if(type!=='textarea')input.type=type;input.name=name;input.required=true;input.value=value;wrap.append(input);return wrap;
}
function selectField(label,name,options,value){
  const wrap=node('label',label,'field'),select=document.createElement('select');select.name=name;select.required=true;
  for(const [id,text] of options){const option=node('option',text);option.value=id;select.append(option);}if(value!==undefined)select.value=value;wrap.append(select);return wrap;
}
function openDialog(title){byId('dialog-title').textContent=title;byId('dialog-content').replaceChildren();if(!byId('drawer').open)byId('drawer').showModal();return byId('dialog-content');}
function formFor(action,fields,convert,after){
  const form=node('form','','form-grid'),errors=node('div');form.append(...fields,errors);const submit=node('button','Save','primary');submit.type='submit';form.append(submit);
  let key=crypto.randomUUID(),lastBody;
  form.onsubmit=async event=>{event.preventDefault();errors.replaceChildren();submit.disabled=true;
    try{const body=convert(Object.fromEntries(new FormData(form)));const encoded=JSON.stringify(body);if(lastBody!==undefined&&lastBody!==encoded)key=crypto.randomUUID();lastBody=encoded;await request(action,body,key);await refresh();await after();}
    catch(error){errorView(errors,error);}finally{submit.disabled=false;}
  };return form;
}
function showOpportunity(id){
  const o=workspace.opportunities.find(x=>x.id===id),body=openDialog(o.title);const summary=node('div','','detail-summary');summary.append(node('strong',money(o.amount_cents)),node('span',`${o.stage} · ${accountName(o.account_id)}`));body.append(summary);
  const stages={Lead:['Lead','Qualified','Lost'],Qualified:['Qualified','Lead','Proposal','Lost'],Proposal:['Proposal','Qualified','Won','Lost'],Won:['Won'],Lost:['Lost','Lead']};
  const edit=formFor('update',[selectField('Stage','stage',stages[o.stage].map(s=>[s,s]),o.stage),field('Opportunity value (USD)','amount','number',o.amount_cents/100),field('Next action','next_action','text',o.next_action),field('Follow-up date','due_date','date',o.due_date)],d=>({id,version:o.version,stage:d.stage,amount_cents:cents(d.amount),next_action:d.next_action,due_date:d.due_date}),()=>showOpportunity(id));
  edit.querySelector('[name=amount]').min='0';edit.querySelector('[name=amount]').step='0.01';if(o.stage==='Won')edit.querySelector('button').disabled=true;body.append(edit);
  const controls=node('div','','actions');
  if(o.stage==='Proposal'){
    const prepare=node('button','Prepare agreement','quiet');prepare.onclick=async()=>{prepare.disabled=true;try{await request('issue',{id,version:o.version});await refresh();showOpportunity(id);}catch(error){body.append(node('p',error.message,'error'));}finally{prepare.disabled=false;}};controls.append(prepare);
  }
  body.append(controls);
  for(const a of workspace.agreements.filter(a=>a.opportunity_id===id).reverse()){
    const closingTerms=o.stage==='Won'&&a.status==='completed'&&a.opportunity_version===o.version-1;
    const box=node('div','','agreement');box.append(node('h3',`Agreement #${a.id}`),node('p',`${a.status==='completed'?'Completion recorded':'Draft'} · ${closingTerms?'Closing terms':a.opportunity_version===o.version?'Current terms':'Earlier terms'}`));
    if(o.stage==='Proposal'&&a.status==='draft'&&a.opportunity_version===o.version){const complete=node('button','Simulate agreement completion','quiet');complete.onclick=async()=>{complete.disabled=true;try{await request('complete',{id,version:o.version,agreement_id:a.id,digest:a.digest});await refresh();showOpportunity(id);}catch(error){box.append(node('p',error.message,'error'));}finally{complete.disabled=false;}};box.append(complete,node('p','Synthetic workflow only. This action records a demo completion; it does not sign a contract.'));}body.append(box);
  }
  body.append(node('h3','Activity'));const note=formFor('note',[field('Add a note','detail','textarea')],d=>({id,detail:d.detail}),()=>showOpportunity(id));note.querySelector('button').textContent='Add note';body.append(note);
  const timeline=node('div','','timeline');for(const a of workspace.activities.filter(a=>a.opportunity_id===id).reverse()){const item=node('p',a.detail,'event');item.append(node('time',new Date(a.created_at).toLocaleString()));timeline.append(item);}body.append(timeline);
}
byId('close-dialog').onclick=()=>byId('drawer').close();
byId('new-opportunity').onclick=()=>{
  if(!workspace.contacts.length){byId('notice').textContent='Add an account and contact before opening an opportunity.';return;}
  const body=openDialog('New opportunity');const account=selectField('Account','account_id',workspace.accounts.map(a=>[a.id,a.name]));
  const contact=selectField('Primary contact','contact_id',[]);
  function sync(){const list=contact.querySelector('select');list.replaceChildren(...workspace.contacts.filter(c=>c.account_id===Number(account.querySelector('select').value)).map(c=>{const option=node('option',`${c.name} · ${c.email}`);option.value=c.id;return option;}));}
  account.querySelector('select').onchange=sync;sync();
  body.append(formFor('opportunities',[field('Opportunity name','title'),account,contact,field('Value (USD)','amount','number','0'),field('Next action','next_action'),field('Follow-up date','due_date','date',new Date().toLocaleDateString('en-CA'))],d=>({title:d.title,account_id:Number(d.account_id),contact_id:Number(d.contact_id),amount_cents:cents(d.amount),next_action:d.next_action,due_date:d.due_date}),()=>byId('drawer').close()));body.querySelector('[name=amount]').min='0';body.querySelector('[name=amount]').step='0.01';
};
byId('new-account').onclick=()=>openDialog('New account').append(formFor('accounts',[field('Company name','name'),field('Company domain','domain')],d=>d,()=>byId('drawer').close()));
byId('new-contact').onclick=()=>openDialog('New contact').append(formFor('contacts',[selectField('Account','account_id',workspace.accounts.map(a=>[a.id,a.name])),field('Contact name','name'),field('Email','email','email')],d=>({...d,account_id:Number(d.account_id)}),()=>byId('drawer').close()));
for(const button of document.querySelectorAll('[data-view]'))button.onclick=()=>{
  currentView=button.dataset.view;for(const b of document.querySelectorAll('[data-view]'))b.classList.toggle('active',b===button);
  for(const view of ['pipeline','accounts','reporting'])byId(view+'-view').hidden=view!==currentView;
  byId('view-title').textContent={pipeline:'Pipeline',accounts:'Accounts',reporting:'Reporting'}[currentView];
};
byId('search').oninput=renderBoard;
refresh().catch(error=>{byId('notice').textContent='Workspace could not be loaded. '+error.message;});
