(() => {
  'use strict';
  const $ = s => document.querySelector(s), root = document.documentElement;
  const c = $('#cTrigger'), a = $('#aTrigger'), menu = $('#spaceMenu'), panel = $('#agentPanel'), dialog = $('#detail');
  const clock = $('.clock-face'), anchor = document.createComment('Home clock');
  clock.after(anchor);
  const profiles = {
    Gandalf: 'Planning and coordinating work with you.', Hermes: 'Research, exploration and continuing conversations.',
    Atomic: 'Repeatable workflows and connected tools.', DeepSeek: 'Code, technical questions and problem solving.'
  };
  const storeKey = 'onepath.agent-workspaces.v1';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storeKey) || '{}'); } catch {}
  const sessions = Object.fromEntries(Object.keys(profiles).map(name => {
    const data = saved?.[name];
    return [name, {draft: typeof data?.draft === 'string' ? data.draft : '', messages: Array.isArray(data?.messages) ? data.messages.filter(x => typeof x === 'string') : [], memory: typeof data?.memory === 'string' ? data.memory : '', tasks: Array.isArray(data?.tasks) ? data.tasks.filter(t => t && typeof t.id === 'string' && typeof t.brief === 'string' && Array.isArray(t.messages) && ['plan','running','waiting','paused','complete','cancelled'].includes(t.state)).map(t => ({...t, state: t.state === 'running' ? 'paused' : t.state})) : [], currentTask: typeof data?.currentTask === 'string' ? data.currentTask : null}];
  }));
  const timers = new Map();
  const activity = Object.fromEntries(Object.keys(profiles).map(name => [name, 'idle']));
  let selected = root.dataset.selectedAgent || null, returnFocus, noticeTimer;
  const save = () => { try { localStorage.setItem(storeKey, JSON.stringify(sessions)); } catch {} };
  const notify = text => { $('#notice').textContent = text; $('#notice').classList.add('show'); clearTimeout(noticeTimer); noticeTimer = setTimeout(() => $('#notice').classList.remove('show'), 3200); };
  const setMenu = (button, content, open) => {
    button.setAttribute('aria-expanded', String(open));
    content.classList.toggle('menu-open', open); content.inert = !open;
  };
  // Both menus stay mounted so exit transitions can complete.
  menu.hidden = false; panel.hidden = false;
  setMenu(c, menu, false); setMenu(a, panel, false);
  const closeMenus = () => { setMenu(c, menu, false); setMenu(a, panel, false); };
  const remember = () => { if (selected) { sessions[selected].draft = $('#taskInput').value; save(); } };
  const render = () => {
    $("#topBar").classList.toggle("agent-working", Object.values(activity).includes("working"));
    $('#workspaceTitle').textContent = selected ? `${selected}'s workspace` : 'Choose an agent';
    $('#workspace .eyebrow').textContent = selected ? `WORKSPACE / ${selected.toUpperCase()}` : 'WORKSPACE';
    $('#agentTitle').textContent = selected || 'Choose an agent';
    $('#agentPurpose').textContent = selected ? profiles[selected] : 'Use A to select an assistant across all views.';
    $('#agentDetails').replaceChildren();
    const route = window.OnePathModels?.resolve(selected);
    for (const [label, value] of [['Connection',route ? `${route.kind} · ${route.model}` : 'No verified model connection'], ['Activity', selected ? activity[selected] : 'Idle'], ['Workspace',selected ? 'Conversation and drafts saved on this browser' : 'No agent selected']]) {
      const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = label; dd.textContent = value; $('#agentDetails').append(dt,dd);
    }
    const conversation = $('#conversation'); conversation.replaceChildren();
    const task = selected ? currentTask() : null;
    const intro = document.createElement('p'); intro.className = 'intro';
    intro.textContent = selected ? `Start with an outcome. This interactive demo saves your work here; no model is connected.` : 'Select an assistant with A to begin.';
    conversation.append(intro);
    for (const item of task ? task.messages : selected ? sessions[selected].messages : []) {
      const text = typeof item === 'string' ? item : item.text;
      const role = typeof item === 'string' ? 'YOU' : item.role;
      const article = document.createElement('article'); article.className = 'message owner-message';
      const label = document.createElement('p'); label.className = 'speaker'; label.textContent = role;
      const body = document.createElement('p'); body.textContent = text; article.append(label,body); conversation.append(article);
    }
    $('#taskInput').value = selected ? sessions[selected].draft : '';
    $('#taskInput').disabled = !selected; $('#composer button').disabled = !selected;
    $('#taskInput').placeholder = selected ? `Message ${selected}…` : 'Select an agent with A…';
    $('#composer label').textContent = selected ? `Message ${selected}` : 'Message selected agent';
    $('#composer .composer-footer span').textContent = 'Local draft · runtime not connected';
    $('#taskStatus').textContent = selected ? `${selected} · ${activity[selected]}` : 'No selected agent';
    $('#taskMeta').textContent = 'No live task running';
    renderTask(task);
    panel.querySelectorAll('[data-agent]').forEach(button => {
      button.setAttribute('aria-label', `Select ${button.dataset.agent}`);
      button.dataset.status = activity[button.dataset.agent];
      button.setAttribute('aria-label', `Select ${button.dataset.agent} · ${activity[button.dataset.agent]}`);
    });
  };
  const closeDetail = () => {
    if (dialog.hidden) return;
    dialog.hidden = true; root.classList.remove('detail-open');
    $('#spaces').inert = false; $('.space-control').inert = false; $('.agent-control').inert = false;
    $('#uiLayer').inert = false; clock.inert = false;
    if (returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
  };
  const openDetail = (title, text) => {
    returnFocus = document.activeElement; closeMenus();
    $('#detailContext').textContent = selected ? `AGENT / ${selected.toUpperCase()}` : 'AGENT';
    $('#detailTitle').textContent = title; $('#detailBody').replaceChildren();
    const p = document.createElement('p'); p.textContent = text; $('#detailBody').append(p);
    dialog.hidden = false; root.classList.add('detail-open');
    $('#spaces').inert = true; $('.space-control').inert = true; $('.agent-control').inert = true;
    $('#uiLayer').inert = true; clock.inert = true; $('#closeDetail').focus();
  };
  const setSpace = (name, update = true) => {
    if (name === 'company') name = 'agents';
    if (!['home','workspace','agents','personal','models'].includes(name)) name = 'home';
    remember(); closeDetail(); closeMenus(); root.dataset.space = name; root.classList.toggle('preview-away', name !== 'home');
    document.querySelectorAll('#spaces .space').forEach(el => el.hidden = el.id !== name);
    menu.querySelectorAll('[data-space]').forEach(el => el.setAttribute('aria-current', el.dataset.space === name ? 'page' : 'false'));
    clock.classList.toggle('preview-clock-compact', name !== 'home');
    if (name === 'home') anchor.parentNode.insertBefore(clock,anchor); else document.body.append(clock);
    $('.center').inert = name !== 'home';
    if (update) history.replaceState(null,'',`#${name}`);
    render();
    window.dispatchEvent(new CustomEvent('onepath:space-change',{detail:{name}}));
  };
  c.addEventListener('click', () => { const open = c.getAttribute('aria-expanded') !== 'true'; closeMenus(); setMenu(c,menu,open); });
  a.addEventListener('click', () => { const open = a.getAttribute('aria-expanded') !== 'true'; closeMenus(); setMenu(a,panel,open); });
  window.addEventListener('onepath:agent-change', event => {
    remember(); selected = profiles[event.detail.name] ? event.detail.name : null;
    closeDetail(); render();
  });
  // Runtime adapters may report activity; selecting an agent never invents work.
  window.addEventListener('onepath:agent-status', event => {
    const {name, status} = event.detail || {};
    if (!profiles[name] || !['idle','working','sleeping','waiting','error'].includes(status)) return;
    remember(); activity[name] = status; render();
  });
  window.addEventListener('onepath:model-config-change', () => { remember(); render(); });
  window.addEventListener('onepath:model-state-change', () => { remember(); render(); });
  document.addEventListener('click', event => {
    if (!event.target.closest('.space-control,.agent-control')) closeMenus();
    const button = event.target.closest('button'); if (!button) return;
    if (button.dataset.space) { setSpace(button.dataset.space); c.focus(); }
    if (button.dataset.action) closeMenus();
    if (button.dataset.resource) {
      const kind = button.dataset.resource;
      if (kind === 'models') { setSpace('models'); return; }
      openDetail(kind === 'models' ? 'Model connection' : kind === 'skills' ? 'Capabilities' : 'Agent memory', !selected ? 'Choose an agent using A.' : kind === 'models' ? `No model is connected for ${selected}. Backend connection will be configured separately.` : kind === 'skills' ? `${profiles[selected]} Tool availability will come from the connected harness.` : 'Private notes for this assistant, saved on this browser.');
      if (kind === 'memory' && selected) {
        const input = document.createElement('textarea'); input.className = 'memory-editor'; input.setAttribute('aria-label',`${selected} memory notes`); input.value = sessions[selected].memory;
        const name = selected; input.addEventListener('input', () => { sessions[name].memory = input.value; save(); }); $('#detailBody').append(input);
      }
    }
    if (button.dataset.detail === 'history') showHistory();
    else if (button.dataset.detail) showTaskDetails();
  });
  $('#closeDetail').addEventListener('click', closeDetail);
  $('#taskInput').addEventListener('input', remember);
  $('#composer').addEventListener('submit', event => {
    event.preventDefault(); if (!selected) return;
    const text = $('#taskInput').value.trim(); if (!text) return;
    const task = currentTask();
    if (task?.state === 'waiting') {
      task.answer = text; task.messages.push({role:'YOU',text});
      task.messages.push({role:`${selected.toUpperCase()} · DEMO`,text:'Thanks. I will include that preference in the sample handoff.'});
      sessions[selected].draft = ''; task.stage = 2; runTask(selected,task);
    } else if (task && !['complete','cancelled'].includes(task.state)) {
      task.messages.push({role:'YOU',text}); sessions[selected].draft = '';
      notify('Added to this demo task. Review the plan or continue using its controls.');
    } else {
      const created = {id:crypto.randomUUID(), brief:text, state:'plan', stage:0, answer:'', created:Date.now(), messages:[{role:'YOU',text},{role:`${selected.toUpperCase()} · DEMO`,text:'Here is a sample plan. Review it before starting the demonstration.'}]};
      sessions[selected].tasks.push(created); sessions[selected].currentTask = created.id; sessions[selected].draft = '';
    }
    save(); render();
  });
  const editable = el => el instanceof Element && el.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]');
  document.addEventListener('keydown', event => {
    if (event.isComposing || event.key === 'Process' || event.keyCode === 229) return;
    if (event.key === 'Escape') { closeDetail(); closeMenus(); return; }
    if (editable(event.target) || event.altKey || event.ctrlKey || event.metaKey || event.repeat) return;
    if (!event.shiftKey && /^[1-5]$/.test(event.key)) {
      event.preventDefault(); event.stopImmediatePropagation();
      setSpace(['home','workspace','agents','personal','models'][Number(event.key)-1]); return;
    }
    if (event.key.startsWith('Arrow')) {
      const open = c.getAttribute('aria-expanded') === 'true' ? menu : a.getAttribute('aria-expanded') === 'true' ? panel : null;
      if (open) {
        const items = [...open.querySelectorAll('button:not(:disabled)')];
        const index = items.indexOf(document.activeElement);
        const next = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? (index+1)%items.length : (index<0 ? items.length-1 : (index-1+items.length)%items.length);
        event.preventDefault(); event.stopImmediatePropagation(); items[next]?.focus(); return;
      }
    }
    if (['a','c'].includes(event.key.toLowerCase())) {
      event.preventDefault(); event.stopImmediatePropagation(); if (dialog.hidden) (event.key.toLowerCase() === 'a' ? a : c).click();
    }
  },true);
  dialog.addEventListener('keydown', event => {
    if(event.key !== 'Tab') return;
    const items = [...dialog.querySelectorAll('button,input,textarea,summary')].filter(el => !el.disabled && el.getClientRects().length);
    const first = items[0], last = items.at(-1);
    if(event.shiftKey && document.activeElement === first){event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement === last){event.preventDefault();first.focus();}
  });
  window.addEventListener('hashchange', () => setSpace(location.hash.slice(1),false));
  // Appearance shares the same centered layer and cannot overlap active content.
  const appearance = $('#themePanel');
  $('#previewShell').append(appearance);
  const syncAppearance = () => {
    const open = !appearance.hidden && !appearance.classList.contains('is-closed');
    root.classList.toggle('appearance-open', open);
    $('#spaces').inert = open || !dialog.hidden;
    $('.center').inert = open || root.dataset.space !== 'home';
  };
  new MutationObserver(syncAppearance).observe(appearance,{attributes:true,attributeFilter:['hidden','class']});

  const currentTask = (name = selected) => name ? sessions[name].tasks.find(t => t.id === sessions[name].currentTask) : null;
  const steps = ['Understand the request', 'Confirm the direction', 'Prepare the handoff'];
  const stateLabels = {plan:'Plan ready',running:'Working',waiting:'Your input needed',paused:'Paused',complete:'Completed',cancelled:'Cancelled'};
  const action = (label, handler) => { const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.addEventListener('click',handler); return button; };
  const addText = (parent, tag, text, cls) => { const node = document.createElement(tag); node.textContent = text; if(cls) node.className=cls; parent.append(node); return node; };
  const taskControls = document.createElement('div'); taskControls.className = 'task-controls'; taskControls.id = 'taskControls';
  $('.task-summary').after(taskControls);
  const taskProgress = document.createElement('ol'); taskProgress.className = 'task-progress'; taskProgress.setAttribute('aria-label','Demo task progress');
  $('.task-summary').before(taskProgress);
  const emptyActions = document.createElement('div'); emptyActions.className = 'task-controls'; $('#conversation').after(emptyActions);
  const emitActivity = name => {
    const active = sessions[name].tasks.find(t => t.state === 'running');
    const waiting = sessions[name].tasks.some(t => t.state === 'waiting');
    activity[name] = active ? 'working' : waiting ? 'waiting' : 'idle';
    panel.querySelector(`[data-agent="${name}"]`).dataset.demo = active ? 'true' : 'false';
  };
  const artifact = (name,task) => `# ${name}: sample task handoff\n\nDEMONSTRATION ONLY — this is a template, not researched or executed work.\n\n## Your request\n${task.brief}\n\n## Your preference\n${task.answer || 'Use a concise outline.'}\n\n## Suggested next steps\n- Confirm the scope and relevant sources or files.\n- Connect an appropriate harness and model.\n- Execute the task with the requested access and approvals.\n- Review the actual output and its supporting evidence.\n\n## Demonstrated in this preview\nPlan review, task progress, user input, pause/resume, and a downloadable result.\n`;
  const runTask = (name,task) => {
    clearTimeout(timers.get(task.id)); task.state = 'running'; emitActivity(name); save();
    timers.set(task.id,setTimeout(() => {
      timers.delete(task.id); if(task.state !== 'running') return;
      if(task.stage < 2) {
        task.stage = 1; task.state = 'waiting';
        task.messages.push({role:`${name.toUpperCase()} · DEMO`,text:'Would you prefer a concise outline or a detailed handoff? Add any priorities below, or use the concise option.'});
      } else {
        task.stage = 3; task.state = 'complete'; task.completed = Date.now();
        task.messages.push({role:`${name.toUpperCase()} · DEMO`,text:'The sample handoff is ready to inspect and download. It includes your request and preference; no external work was performed.'});
      }
      remember(); emitActivity(name); save(); render();
    },3200));
  };
  const renderTask = task => {
    emptyActions.replaceChildren(); taskControls.replaceChildren(); taskProgress.replaceChildren();
    const submit = $('#composer button'); submit.textContent = task?.state === 'waiting' ? 'Reply ↗' : task && !['complete','cancelled'].includes(task.state) ? 'Add note ↗' : 'Start demo task ↗';
    submit.setAttribute('aria-label',submit.textContent.replace(' ↗',''));
    $('#composer .composer-footer span').textContent = 'Interactive demo · saved on this browser';
    $('.task-summary').hidden = !task; taskProgress.hidden = !task;
    if(!task) {
      if(selected) emptyActions.append(action('Try an example',() => { $('#taskInput').value = ({Hermes:'Compare three approaches to planning a weekend trip.',Atomic:'Create a repeatable weekly reading workflow.',DeepSeek:'Plan a small personal budgeting web app.',Gandalf:'Help me turn a product idea into a practical plan.'})[selected]; remember(); $('#taskInput').focus(); }));
      return;
    }
    $('#taskStatus').textContent = `${stateLabels[task.state]} · demo`;
    $('#taskMeta').textContent = task.state === 'running' ? 'Simulating progress — no model is running' : task.state === 'paused' ? 'Your place is saved. Resume when ready.' : task.state === 'complete' ? 'Sample handoff ready to review' : 'Your work stays with this agent';
    steps.forEach((text,index) => { const li=addText(taskProgress,'li',text); li.dataset.state = task.stage > index ? 'done' : task.stage === index ? 'current' : 'pending'; if(task.stage===index) li.setAttribute('aria-current','step'); });
    const name = selected;
    if(task.state==='plan') taskControls.append(action('Review plan',showTaskDetails),action('Approve demo plan',() => { remember(); runTask(name,task); render(); }));
    if(task.state==='running') taskControls.append(action('Pause',() => { clearTimeout(timers.get(task.id)); timers.delete(task.id); task.state='paused'; remember(); emitActivity(name); save(); render(); }));
    if(task.state==='paused') taskControls.append(action('Resume demo',() => { remember();runTask(name,task);render(); }));
    if(task.state==='waiting') taskControls.append(action('Use a concise outline',() => { task.answer='Concise outline';task.stage=2;remember();runTask(name,task);render(); }));
    if(!['complete','cancelled'].includes(task.state)) taskControls.append(action('Cancel task',() => { clearTimeout(timers.get(task.id));timers.delete(task.id);task.state='cancelled';remember();emitActivity(name);save();render(); }));
    if(task.state==='complete') taskControls.append(action('View result',showTaskDetails));
    if(['complete','cancelled'].includes(task.state)) taskControls.append(action('New task',() => { remember();sessions[name].currentTask=null;save();render();$('#taskInput').focus(); }));
  };
  const showTaskDetails = () => {
    const task=currentTask();
    if(!task) { openDetail('No current task','Start a demo task in Workspace to explore the complete journey.');return; }
    const name=selected;
    openDetail(task.state==='complete' ? 'Your sample handoff' : 'Review the task',task.brief);
    const body=$('#detailBody');
    addText(body,'p','INTERACTIVE DEMO · NO EXTERNAL EXECUTION','demo-label');
    if(task.state==='complete') {
      addText(body,'pre',artifact(name,task),'artifact-preview');
      body.append(action('Download handoff (.md)',() => {
        const url=URL.createObjectURL(new Blob([artifact(name,task)],{type:'text/markdown;charset=utf-8'}));
        const link=document.createElement('a');link.href=url;link.download=`onepath-${name.toLowerCase()}-demo.md`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }));
    } else {
      const list=document.createElement('ol');body.append(list);
      steps.forEach(text=>addText(list,'li',text));
      addText(body,'p','The demonstration pauses for your preference, then creates a sample handoff. No tools, external services, or paid credits are used.');
      if(task.state==='plan') body.append(action('Approve demo plan',()=>{closeDetail();remember();runTask(name,task);render();}));
    }
  };
  const showHistory = () => {
    openDetail('Past tasks',selected ? `Tasks saved for ${selected} on this browser.` : 'Choose an agent first.');
    if(!selected) return;
    const body=$('#detailBody'),name=selected;
    if(!sessions[name].tasks.length) addText(body,'p','No saved tasks yet.');
    [...sessions[name].tasks].reverse().forEach(task => {
      const item=document.createElement('div');item.className='history-task';
      addText(item,'p',task.brief);addText(item,'small',`${stateLabels[task.state]} · demo · ${new Date(task.created).toLocaleDateString()}`);
      item.append(action('Open task',()=>{remember();sessions[name].currentTask=task.id;save();closeDetail();render();}));body.append(item);
    });
  };
  // Render before navigation to avoid overwriting a saved draft with initial markup.
  render(); setSpace(location.hash.slice(1) || 'home',false);
})();
