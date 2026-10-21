(() => {
  'use strict';
  const $ = s => document.querySelector(s), root = document.documentElement;
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(key,JSON.stringify(value)); return true; } catch { return false; } };
  const el = (tag, text, cls) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; };
  const button = (text, fn, label) => { const b=el('button',text);b.type='button';if(label)b.setAttribute('aria-label',label);b.addEventListener('click',fn);return b; };
  const announce = text => { $('#notice').textContent=text;$('#notice').classList.add('show');setTimeout(()=>$('#notice').classList.remove('show'),4000); };
  const section = (id, title, subtitle) => { const s=el('section',undefined,'space settings-space');s.id=id;s.hidden=true;s.setAttribute('aria-labelledby',id+'Title');const h=el('header',undefined,'space-heading');const d=el('div');d.append(el('p',id==='personal'?'YOUR SPACE / 4':'MODELS / 5','eyebrow'));const t=el('h1',title);t.id=id+'Title';d.append(t);h.append(d);s.append(h,el('p',subtitle,'settings-intro'));$('#spaces').append(s);return s; };
  const field = (parent, label, value='', type='text', options) => { const wrap=el('label',undefined,'settings-field');wrap.append(el('span',label));let input;
    if(options){input=el('select');options.forEach(([v,t])=>{const o=el('option',t);o.value=v;input.append(o);});}
    else input=el(type==='textarea'?'textarea':'input');
    if(input.tagName==='INPUT') input.type=type;
    if(input.tagName==='TEXTAREA') input.rows=3;
    input.setAttribute('aria-label',label);input.value=value;wrap.append(input);parent.append(wrap);return input;
  };
  const tabs = (parent, items) => {const bar=el('div',undefined,'settings-tabs');bar.setAttribute('role','tablist');parent.append(bar);const panes=[];
    items.forEach(([title,build],index)=>{const pane=el('div',undefined,'settings-pane');pane.id=parent.id+'-pane-'+index;pane.setAttribute('role','tabpanel');pane.hidden=index!==0;const b=button(title,()=>{panes.forEach(([p,t])=>{p.hidden=p!==pane;t.setAttribute('aria-selected',String(p===pane));t.tabIndex=p===pane?0:-1;});});b.id=pane.id+'-tab';b.setAttribute('role','tab');b.setAttribute('aria-controls',pane.id);b.setAttribute('aria-selected',String(index===0));b.tabIndex=index===0?0:-1;pane.setAttribute('aria-labelledby',b.id);bar.append(b);parent.append(pane);panes.push([pane,b]);build(pane);});
    bar.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;const i=panes.findIndex(([,b])=>b===document.activeElement);const j=e.key==='Home'?0:e.key==='End'?panes.length-1:(i+(e.key==='ArrowRight'?1:-1)+panes.length)%panes.length;e.preventDefault();panes[j][1].click();panes[j][1].focus();});
  };

  // Keep Appearance reachable independently of navigation.
  const appearance=$('#spaceMenu [data-action="appearance"]');
  appearance.classList.add('appearance-corner');appearance.setAttribute('aria-label','Appearance · T');$('#previewShell').append(appearance);
  [...$('#spaceMenu').querySelectorAll('[data-space]')].forEach((b,i)=>{b.querySelector('.tip').textContent=`${i+1} · ${['Home','Workspace','Agents','Your space','Models'][i]}`;});

  const personal=section('personal','Your space.','Your profile, projects and plans — saved on this browser.');
  const existing=read('onepath.personal.v1',{});
  const profile={name:typeof existing.name==='string'?existing.name:($('#displayName')?.value||''),about:existing.about||'',project:existing.project||'',trip:existing.trip||'',timezone:existing.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone};
  let plans=Array.isArray(existing.plans)?existing.plans.filter(p=>p&&typeof p.title==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(p.date)):[];
  const savePersonal=()=>write('onepath.personal.v1',{...profile,plans});
  tabs(personal,[['About me',pane=>{
    const form=el('form',undefined,'settings-form');pane.append(form);
    const name=field(form,'Your name',profile.name);name.maxLength=80;
    const about=field(form,'About me',profile.about,'textarea');about.maxLength=3000;
    const project=field(form,'Current project',profile.project);project.maxLength=200;
    const trip=field(form,'Upcoming trip',profile.trip);trip.maxLength=200;
    const zone=field(form,'Planning time zone',profile.timezone);zone.readOnly=true;
    const save=el('button','Save profile ↗','settings-primary');save.type='submit';form.append(save,el('p','Plans use local dates. Your profile is private to this browser.','settings-note'));
    form.addEventListener('submit',e=>{e.preventDefault();Object.assign(profile,{name:name.value.trim(),about:about.value.trim(),project:project.value.trim(),trip:trip.value.trim()});if(!savePersonal()){announce('Could not save. Browser storage is full or unavailable.');return;}const greeting=$('#displayName');if(greeting){greeting.value=profile.name;greeting.dispatchEvent(new Event('input',{bubbles:true}));}announce('Profile saved.');});
  }],['Calendar & plans',pane=>{
    const today=new Date();let month=new Date(today.getFullYear(),today.getMonth(),1),chosen=key(today);
    function key(d){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
    const nav=el('div',undefined,'calendar-nav'),heading=el('span');nav.append(button('‹',()=>shift(-1),'Previous month'),heading,button('›',()=>shift(1),'Next month'),button('Today',()=>{month=new Date(today.getFullYear(),today.getMonth(),1);chosen=key(today);draw();}));pane.append(nav);
    const calendar=el('div',undefined,'calendar-grid');calendar.setAttribute('role','group');calendar.setAttribute('aria-label','Calendar');pane.append(calendar);
    const agendaTitle=el('h2'),agenda=el('div',undefined,'plan-list');pane.append(agendaTitle,agenda);
    const form=el('form',undefined,'settings-form plan-form');pane.append(form);
    const title=field(form,'Plan something','');title.required=true;title.maxLength=200;
    const date=field(form,'Date',chosen,'date');date.required=true;
    const type=field(form,'Category','personal','text',[['personal','Personal'],['project','Project'],['trip','Trip']]);
    const add=el('button','Add plan ↗','settings-primary');add.type='submit';form.append(add);
    function shift(step){month.setMonth(month.getMonth()+step);draw();}
    function draw(){heading.textContent=month.toLocaleDateString(undefined,{month:'long',year:'numeric'});calendar.replaceChildren();['Mo','Tu','We','Th','Fr','Sa','Su'].forEach(t=>calendar.append(el('span',t,'weekday')));const start=(month.getDay()+6)%7;for(let i=0;i<start;i++)calendar.append(el('span'));const count=new Date(month.getFullYear(),month.getMonth()+1,0).getDate();for(let d=1;d<=count;d++){const value=key(new Date(month.getFullYear(),month.getMonth(),d));const b=button(String(d),()=>{chosen=value;date.value=chosen;draw();calendar.querySelector(`[data-date="${value}"]`)?.focus();});b.dataset.date=value;b.setAttribute('aria-pressed',String(value===chosen));b.tabIndex=value===chosen||(!chosen.startsWith(key(month).slice(0,7))&&d===1)?0:-1;b.setAttribute('aria-label',`${value}${plans.some(p=>p.date===value)?', has plans':''}`);if(value===key(today))b.setAttribute('aria-current','date');if(plans.some(p=>p.date===value))b.classList.add('has-plans');calendar.append(b);}agendaTitle.textContent=new Date(chosen+'T12:00:00').toLocaleDateString(undefined,{weekday:'long',month:'short',day:'numeric'});agenda.replaceChildren();const entries=plans.filter(p=>p.date===chosen);if(!entries.length)agenda.append(el('p','Nothing planned for this date.','settings-note'));entries.forEach(p=>{const row=el('div',undefined,'plan-row');const complete=button(p.done?'✓':'○',()=>{p.done=!p.done;persist();},p.done?'Mark incomplete':'Mark complete');complete.setAttribute('aria-pressed',String(!!p.done));row.append(complete,el('span',`${p.title} · ${p.type||'Personal'}`,p.done?'plan-done':''),button('×',()=>{plans=plans.filter(x=>x.id!==p.id);persist();},'Remove '+p.title));agenda.append(row);});}
    function persist(){if(!savePersonal())announce('Could not save this plan.');draw();}
    form.addEventListener('submit',e=>{e.preventDefault();if(!title.value.trim()||!date.validity.valid)return;chosen=date.value;plans.push({id:crypto.randomUUID(),title:title.value.trim(),date:chosen,type:type.value,done:false});month=new Date(chosen+'T12:00:00');month.setDate(1);title.value='';persist();announce('Plan added.');});
    calendar.addEventListener('keydown',e=>{const step={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[e.key];if(!step||!e.target.dataset.date)return;e.preventDefault();const d=new Date(e.target.dataset.date+'T12:00:00');d.setDate(d.getDate()+step);chosen=key(d);month=new Date(d.getFullYear(),d.getMonth(),1);date.value=chosen;draw();calendar.querySelector(`[data-date="${chosen}"]`).focus();});draw();
  }]]);

  const models=section('models','Models & connections.','Local models, cloud connections and automatic routing.');
  const stored=read('onepath.models.v1',{});
  const config={mode:['local','cloud','fusion'].includes(stored.mode)?stored.mode:'fusion',local:stored.local||'',cloud:stored.cloud||'',fallback:stored.fallback===true,routes:stored.routes||{},connections:Array.isArray(stored.connections)?stored.connections.filter(c=>c&&typeof c.id==='string'&&typeof c.name==='string'&&['local','cloud'].includes(c.kind)).map(({id,name,kind,protocol,endpoint,models,credentialRef})=>({id,name,kind,protocol,endpoint,models:Array.isArray(models)?models.filter(m=>typeof m==='string'):[],credentialRef:credentialRef||''})):[]};
  const credentials=new Map(),statuses=new Map();let requested=new Map(),refreshModels=()=>{},refreshConnections=()=>{};
  const saveConfig=()=>write('onepath.models.v1',config);
  const cloudMode=button('CLOUD',()=>{});cloudMode.dataset.mode='cloud';cloudMode.setAttribute('aria-pressed','false');cloudMode.append(el('small','Your cloud connections'));$('#modeMenu .mode-note').before(cloudMode);
  cloudMode.addEventListener('click',()=>{config.mode='cloud';saveConfig();window.dispatchEvent(new CustomEvent('onepath:model-config-change',{detail:snapshot()}));$('#modeToggle').click();});
  window.addEventListener('onepath:execution-mode',event=>{config.mode=event.detail.mode;saveConfig();const control=$('#models-pane-0 select');if(control)control.value=config.mode;});
  const modelOptions=kind=>[['','Automatic · choose an available model'],...config.connections.filter(c=>c.kind===kind).flatMap(c=>c.models.map(m=>[c.id+'::'+m,c.name+' / '+m]))];
  const snapshot=()=>JSON.parse(JSON.stringify(config));
  const resolve = (agent) => {
    if(!['Gandalf','Hermes','Atomic','DeepSeek'].includes(agent))return null;
    if(agent==='Gandalf'&&config.mode==='local')return null;
    const preference=agent==='Gandalf'?'cloud':config.mode==='local'?'local':config.routes[agent]==='local'?'local':config.routes[agent]==='cloud'?'cloud':config.mode;
    const find=kind=>{const available=config.connections.filter(c=>c.kind===kind&&statuses.get(c.id)?.state==='connected');const wanted=config[kind];for(const c of available){const m=c.models.find(id=>c.id+'::'+id===wanted);if(m)return{connectionId:c.id,model:m,kind};}const c=available.find(c=>c.models.length);return c?{connectionId:c.id,model:c.models[0],kind}:null;};
    if(preference==='local'||preference==='cloud')return find(preference);
    return find('local')||(config.fallback?find('cloud'):null);
  };
  window.OnePathModels=Object.freeze({getConfiguration:snapshot,resolve});
  const request=(operation,id)=>{const requestId=crypto.randomUUID();requested.set(requestId,{id,operation});announce('Waiting for the backend adapter.');window.dispatchEvent(new CustomEvent('onepath:model-request',{detail:{requestId,operation,connection:config.connections.find(c=>c.id===id),configuration:snapshot(),apiKey:credentials.get(id)||undefined}}));setTimeout(()=>{if(requested.has(requestId)){requested.delete(requestId);announce('Backend unavailable. Your configuration is saved for later.');}},8000);};
  tabs(models,[['Routing',pane=>{
    const form=el('form',undefined,'settings-form');pane.append(form);
    const mode=field(form,'System preference',config.mode,'text',[['local','Local · keep work on this device'],['fusion','Fusion · local first, cloud when allowed'],['cloud','Cloud · use your cloud connections']]);
    const local=field(form,'Preferred local model',config.local,'text',modelOptions('local'));
    const cloud=field(form,'Preferred cloud model',config.cloud,'text',modelOptions('cloud'));
    const fallback=field(form,'Allow cloud fallback in Fusion',config.fallback?'yes':'no','text',[['no','Ask before sending work to cloud'],['yes','Allow cloud fallback']]);
    form.append(el('p','Automatic routing follows your preference, then a model reported available by its connection. Local mode never falls back to cloud.','settings-note'));
    const overrides=el('details');overrides.append(el('summary','Agent preferences'));const selects={};overrides.append(el('p','Gandalf uses cloud models. In Local mode, Gandalf waits for a change of preference.','settings-note'));['Hermes','Atomic','DeepSeek'].forEach(name=>{selects[name]=field(overrides,name,config.routes[name]||'auto','text',[['auto','Follow system'],['local','Prefer local'],['cloud','Prefer cloud']]);});form.append(overrides);
    const save=el('button','Save model preferences ↗','settings-primary');save.type='submit';form.append(save);
    form.addEventListener('submit',e=>{e.preventDefault();Object.assign(config,{mode:mode.value,local:local.value,cloud:cloud.value,fallback:fallback.value==='yes',routes:Object.fromEntries(Object.entries(selects).map(([n,s])=>[n,s.value]))});if(saveConfig()){window.dispatchEvent(new CustomEvent('onepath:model-config-change',{detail:snapshot()}));announce('Model preferences saved. Runtime connection is pending.');}else announce('Could not save model preferences.');});
    refreshModels=()=>{for(const [input,kind]of [[local,'local'],[cloud,'cloud']]){const value=config[kind];input.replaceChildren();modelOptions(kind).forEach(([v,t])=>{const o=el('option',t);o.value=v;input.append(o);});input.value=value;if(input.selectedIndex<0)input.value='';}};
  }],['Connections',pane=>{
    const list=el('div',undefined,'connection-list');pane.append(list);
    let editing=null;
    const form=el('form',undefined,'settings-form');pane.append(form);
    const title=el('h2','Add a connection');form.append(title);
    const name=field(form,'Connection name');name.required=true;name.maxLength=80;
    const kind=field(form,'Location','local','text',[['local','Local server'],['cloud','Cloud API']]);
    const protocol=field(form,'API format','openai-compatible','text',[['openai-compatible','OpenAI compatible'],['ollama','Ollama'],['anthropic','Anthropic'],['gemini','Gemini']]);
    const endpoint=field(form,'Base URL','','url');endpoint.required=true;endpoint.placeholder='http://localhost:11434';
    const modelIds=field(form,'Model IDs · one per line','','textarea');modelIds.placeholder='Enter IDs now, or discover them after connecting';
    const key=field(form,'API key · temporary for this session','','password');key.autocomplete='off';key.spellcheck=false;
    const ref=field(form,'Saved credential name · optional');ref.placeholder='Reference to a future backend secret';
    const advanced=el('details');advanced.append(el('summary','Advanced connection settings'),protocol.closest('label'),ref.closest('label'));form.append(advanced);
    const actions=el('div',undefined,'settings-actions');const save=el('button','Save connection ↗','settings-primary');save.type='submit';actions.append(save,button('Cancel edit',reset));form.append(actions,el('p','Connection settings and model IDs are saved here. Keys remain in memory and clear on reload. Connect and Discover need the backend adapter; they do not contact a provider from this preview.','settings-note'));
    function reset(){editing=null;form.reset();title.textContent='Add a connection';key.value='';}
    function draw(){list.replaceChildren();if(!config.connections.length)list.append(el('p','Add your local server or a cloud provider to begin.','settings-note'));config.connections.forEach(c=>{const row=el('article',undefined,'connection-row');row.append(el('h2',c.name),el('p',`${c.kind} · ${statuses.get(c.id)?.state||'Not connected'} · ${c.models.length} models`,'settings-note'));const actions=el('div',undefined,'settings-actions');actions.append(button('Edit',()=>{editing=c.id;title.textContent='Edit connection';name.value=c.name;kind.value=c.kind;protocol.value=c.protocol||'openai-compatible';endpoint.value=c.endpoint||'';modelIds.value=c.models.join('\n');key.value=credentials.get(c.id)||'';ref.value=c.credentialRef||'';name.focus();}),button('Connect',()=>request('connect',c.id)),button('Discover models',()=>request('discover',c.id)),button('Remove',()=>{for(const [requestId,pending] of requested)if(pending.id===c.id)requested.delete(requestId);config.connections=config.connections.filter(x=>x.id!==c.id);credentials.delete(c.id);statuses.delete(c.id);for(const k of ['local','cloud'])if(config[k].startsWith(c.id+'::'))config[k]='';saveConfig();if(editing===c.id)reset();draw();refreshModels();paintSatellite();}));row.append(actions);list.append(row);});}
    form.addEventListener('submit',e=>{e.preventDefault();if(!name.value.trim())return;let url;try{url=new URL(endpoint.value);}catch{announce('Enter a valid base URL.');return;}if(!['http:','https:'].includes(url.protocol)||url.username||url.password){announce('Use an HTTP or HTTPS base URL without embedded credentials.');return;}if(url.search||url.hash){announce('Use a base URL without query parameters or a fragment.');return;}if(kind.value==='cloud'&&url.protocol!=='https:'){announce('Cloud connections require HTTPS.');return;}const id=editing||crypto.randomUUID();for(const [requestId,pending] of requested)if(pending.id===id)requested.delete(requestId);const c={id,name:name.value.trim(),kind:kind.value,protocol:protocol.value,endpoint:url.href.replace(/\/$/,''),models:[...new Set(modelIds.value.split('\n').map(s=>s.trim()).filter(Boolean))],credentialRef:ref.value.trim()};const i=config.connections.findIndex(c=>c.id===id);if(i<0)config.connections.push(c);else config.connections[i]=c;if(key.value)credentials.set(id,key.value);else credentials.delete(id);statuses.delete(id);saveConfig();reset();draw();refreshModels();paintSatellite();announce('Connection settings saved. Not connected yet.');});
    window.addEventListener('onepath:model-response',event=>{const d=event.detail||{},pending=requested.get(d.requestId);if(!pending)return;requested.delete(d.requestId);const c=config.connections.find(c=>c.id===pending.id);if(!c)return;if(d.ok===true){if(pending.operation==='connect')statuses.set(c.id,{state:'connected'});if(Array.isArray(d.models)){c.models=[...new Set(d.models.filter(m=>typeof m==='string'))];saveConfig();refreshModels();}announce(pending.operation==='connect'?'Connection confirmed.':'Model list updated.');}else{if(pending.operation==='connect')statuses.set(c.id,{state:'error'});announce('Connection request failed. Check your configuration.');}draw();paintSatellite();});refreshConnections=draw;draw();
  }]]);

  const satellite=button('',()=>{$('#spaceMenu [data-space="models"]').click();},'Cloud disconnected · Open Models');satellite.id='cloudSatellite';satellite.className='cloud-satellite';const ascii=el('pre');ascii.setAttribute('aria-hidden','true');satellite.append(ascii,el('span','Cloud offline','satellite-label'));$('#previewShell').append(satellite);
  const frames={offline:['  \\   /\n[=]<>[=]\n   / \\ .','  \\   /\n[=]<>[=]\n   / \\  '],connected:['  \\ ) /\n[=]<>[=]\n   / \\  ','  \\ ))/\n[=]<>[=]\n   / \\  ','  \\  /\n[=]<>[=]\n   / \\  ']};let frame=0;
  function paintSatellite(){const online=config.connections.some(c=>c.kind==='cloud'&&statuses.get(c.id)?.state==='connected');satellite.dataset.state=online?'connected':'offline';satellite.setAttribute('aria-label',online?'Cloud connected · Open Models':'Cloud disconnected · Open Models');satellite.querySelector('span').textContent=online?'Cloud linked':'Cloud offline';ascii.textContent=frames[online?'connected':'offline'][frame%frames[online?'connected':'offline'].length];}
  setInterval(()=>{if(document.hidden||root.classList.contains('no-motion')||matchMedia('(prefers-reduced-motion: reduce)').matches)return;frame++;paintSatellite();},650);paintSatellite();
  window.addEventListener('onepath:model-response',()=>window.dispatchEvent(new CustomEvent('onepath:model-state-change')));
  window.addEventListener('onepath:model-status',event=>{const {connectionId,state}=event.detail||{};if(!config.connections.some(c=>c.id===connectionId)||!['disconnected','error'].includes(state))return;statuses.set(connectionId,{state});refreshConnections();paintSatellite();window.dispatchEvent(new CustomEvent('onepath:model-state-change'));});
  window.dispatchEvent(new CustomEvent('onepath:model-config-change',{detail:snapshot()}));
  const sync=()=>{const name=root.dataset.space;for(const s of [personal,models])s.hidden=s.id!==name;if(root.classList.contains('appearance-open'))$('#themePanel .panel-close')?.click();};window.addEventListener('onepath:space-change',sync);sync();
  const help=el('p','1 Home · 2 Workspace · 3 Agents · 4 You · 5 Models','keyboard-hint');$('#previewShell').append(help);
  // One stroke system; individual motifs retain a recognisable silhouette.
  const marks={
    home:'<path d="m4 13 12-9 12 9M7 11v16h18V11M12 27v-9h8v9"/><path class="icon-signal" d="M12 13h8"/>',
    workspace:'<rect x="4" y="5" width="24" height="21" rx="2"/><path d="M4 11h24M10 16l-3 3 3 3M14 22h7"/><path class="icon-signal" d="M8 8h1m3 0h1"/>',
    agents:'<path d="m16 4 5 5-5 5-5-5zM4 21l5-5 5 5-5 5zm14 0 5-5 5 5-5 5zM16 14v3m-7-1 3-3m8 0 3 3"/><circle class="icon-signal" cx="16" cy="27" r="1"/>',
    personal:'<circle cx="16" cy="10" r="5"/><path d="M6 27v-3a10 10 0 0 1 20 0v3M10 27h12"/><path class="icon-signal" d="m25 5 2-2m-1 7h3"/>',
    models:'<rect x="9" y="9" width="14" height="14" rx="2"/><path d="M13 3v6m6-6v6M13 23v6m6-6v6M3 13h6m-6 6h6m14-6h6m-6 6h6"/><path class="icon-signal" d="m16 12 4 4-4 4-4-4z"/>',
    wizard:'<path d="m8 17 5-12 8 2-3 3 5 7M5 18q10-5 21 0M9 20l2 6 5 3 5-3 2-6M12 21h1m6 0h1M16 23v2"/><path class="icon-signal" d="m25 2 1 3 3 1-3 1-1 3-1-3-3-1 3-1z"/>',
    atomic:'<g class="icon-orbit"><ellipse cx="16" cy="16" rx="14" ry="5"/><ellipse cx="16" cy="16" rx="14" ry="5" transform="rotate(60 16 16)"/><ellipse cx="16" cy="16" rx="14" ry="5" transform="rotate(120 16 16)"/></g><circle cx="16" cy="16" r="2" fill="currentColor"/><circle class="icon-signal" cx="28" cy="13" r="1.5" fill="currentColor"/>'
  };
  const icon=kind=>{const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 32 32');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.5');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');svg.setAttribute('aria-hidden','true');svg.classList.add('nav-mark','mark-'+kind);svg.innerHTML=marks[kind];return svg;};
  $('#spaceMenu').querySelectorAll('[data-space]').forEach(b=>{b.querySelector('span[aria-hidden]').replaceChildren(icon(b.dataset.space));});
  panelIcon('Gandalf','wizard');panelIcon('Atomic','atomic');
  function panelIcon(name,kind){const glyph=$(`#agentPanel [data-agent="${name}"] .agent-glyph`);glyph.replaceChildren(icon(kind));}
})();
