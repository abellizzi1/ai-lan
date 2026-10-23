const $ = (selector) => document.querySelector(selector);
let currentUser = null;
let chats = [];
let activeId = null;
let activeChat = null;
let sending = false;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.text();
  let data = null;
  try { data = body ? JSON.parse(body) : null; } catch { /* Use the HTTP status below. */ }
  if (!response.ok) {
    throw new Error(data?.detail || data?.error?.message || `Request failed (${response.status}).`);
  }
  return data;
}

function setChatStatus(message, error = false) {
  $('#chat-status').textContent = message;
  $('#chat-status').classList.toggle('error', error);
}
function closeSidebar() {
  $('#sidebar').classList.remove('open');
  $('#mobile-shade').classList.remove('open');
}
function renderList() {
  const list = $('#chat-list');
  list.replaceChildren();
  if (!chats.length) {
    const empty = document.createElement('p');
    empty.className = 'side-empty';
    empty.textContent = 'Your conversations will appear here.';
    list.append(empty);
    return;
  }
  for (const chat of chats) {
    const button = document.createElement('button');
    button.className = `chat-item${chat.id === activeId ? ' active' : ''}`;
    button.textContent = chat.title || 'New chat';
    button.title = chat.title || 'New chat';
    button.onclick = () => selectChat(chat.id);
    list.append(button);
  }
}
function renderThread() {
  const thread = $('#thread');
  thread.replaceChildren();
  $('#chat-title').textContent = activeChat?.title || 'New chat';
  $('#delete-chat').hidden = !activeChat;
  if (!activeChat?.messages.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.innerHTML = '<div class="empty-orb" aria-hidden="true">✳</div><h1>What’s on your mind?</h1><p>Start a conversation with your local AI gateway.</p>';
    const suggestions = document.createElement('div');
    suggestions.className = 'suggestions';
    for (const text of ['Help me plan dinner', 'Explain something simply', 'Brainstorm an idea']) {
      const button = document.createElement('button');
      button.className = 'suggestion';
      button.textContent = text;
      button.onclick = () => { $('#prompt').value = text; $('#prompt').focus(); };
      suggestions.append(button);
    }
    empty.append(suggestions);
    thread.append(empty);
    return;
  }
  for (const message of activeChat.messages) {
    const row = document.createElement('article');
    row.className = `message ${message.role}`;
    const body = document.createElement('div');
    body.className = 'message-body';
    const name = document.createElement('div');
    name.className = 'message-name';
    name.textContent = message.role === 'assistant' ? 'Hearth AI' : 'You';
    const content = document.createElement('div');
    content.className = `message-text${message.role === 'assistant' ? ' markdown' : ''}`;
    if (message.role === 'assistant' && typeof message.html === 'string') {
      content.innerHTML = message.html;
      for (const link of content.querySelectorAll('a')) {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
    } else {
      content.textContent = message.content;
    }
    body.append(name, content);
    if (message.role === 'assistant') {
      const avatar = document.createElement('span');
      avatar.className = 'avatar';
      avatar.textContent = '✳';
      row.append(avatar);
    }
    row.append(body);
    thread.append(row);
  }
  $('#scroll-area').scrollTop = $('#scroll-area').scrollHeight;
}
function render() { renderList(); renderThread(); }

async function refreshChats() {
  chats = await api('/v1/chats');
  renderList();
}
async function selectChat(id) {
  try {
    const chat = await api(`/v1/chats/${encodeURIComponent(id)}`);
    activeId = chat.id;
    activeChat = chat;
    render();
    setChatStatus('');
    closeSidebar();
  } catch (error) { setChatStatus(error.message, true); }
}
async function migrateBrowserChats() {
  const prefix = `hearth-ai:${encodeURIComponent(currentUser.username)}:`;
  const oldKey = `${prefix}chats`;
  let oldChats;
  try { oldChats = JSON.parse(localStorage.getItem(oldKey) || '[]'); }
  catch { return true; }
  if (!Array.isArray(oldChats) || !oldChats.length) return true;
  try {
    for (const chat of oldChats) {
      await api('/v1/chats/import', {
        method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({source_id: chat.id, title: chat.title || 'New chat', messages: chat.messages}),
      });
    }
    localStorage.removeItem(oldKey);
    localStorage.removeItem(`${prefix}active`);
    return true;
  } catch { return false; }
}
async function showSession() {
  try {
    currentUser = await api('/auth/me');
  } catch {
    currentUser = null;
    $('#app').hidden = true;
    $('#login-screen').hidden = false;
    return;
  }
  $('#user-name').textContent = currentUser.username;
  $('#model-pill').textContent = currentUser.default_model || 'AI model';
  $('#admin-button').hidden = !currentUser.is_admin;
  $('#login-screen').hidden = true;
  $('#app').hidden = false;
  const migrated = await migrateBrowserChats();
  try {
    await refreshChats();
    if (chats.length) await selectChat(chats[0].id);
    else { activeId = null; activeChat = null; render(); }
    if (!migrated) setChatStatus('Some chats saved in this browser could not be synced yet.', true);
  } catch (error) { setChatStatus(`Could not load conversations: ${error.message}`, true); }
}

$('#login-form').onsubmit = async (event) => {
  event.preventDefault();
  $('#login-error').textContent = '';
  try {
    await api('/auth/login', {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({username: $('#username').value, password: $('#password').value}),
    });
    $('#password').value = '';
    await showSession();
  } catch (error) { $('#login-error').textContent = error.message || 'Could not sign in.'; }
};
$('#logout').onclick = async () => {
  await fetch('/auth/logout', {method: 'POST'}).catch(() => {});
  currentUser = null;
  chats = [];
  activeId = null;
  activeChat = null;
  closeSidebar();
  $('#app').hidden = true;
  $('#login-screen').hidden = false;
  $('#login-error').textContent = '';
};
$('#new-chat').onclick = () => {
  activeId = null;
  activeChat = null;
  render();
  setChatStatus('');
  closeSidebar();
  $('#prompt').focus();
};
$('#delete-chat').onclick = async () => {
  if (!activeChat || !confirm('Delete this conversation from your account?')) return;
  try {
    await api(`/v1/chats/${encodeURIComponent(activeId)}`, {method: 'DELETE'});
    activeId = null;
    activeChat = null;
    await refreshChats();
    if (chats.length) await selectChat(chats[0].id);
    else render();
  } catch (error) { setChatStatus(error.message, true); }
};
$('#menu-button').onclick = () => { $('#sidebar').classList.add('open'); $('#mobile-shade').classList.add('open'); };
$('#mobile-shade').onclick = closeSidebar;
$('#admin-button').onclick = () => { $('#admin-status').textContent = ''; $('#admin-dialog').showModal(); closeSidebar(); };
$('#close-admin').onclick = () => $('#admin-dialog').close();
$('#admin-form').onsubmit = async (event) => {
  event.preventDefault();
  try {
    const data = await api('/auth/users', {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({username: $('#new-username').value, password: $('#new-password').value}),
    });
    $('#admin-status').textContent = `Account ${data.username} created.`;
    $('#new-username').value = '';
    $('#new-password').value = '';
  } catch (error) { $('#admin-status').textContent = error.message; }
};
$('#prompt').onkeydown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('#composer').requestSubmit();
  }
};
$('#composer').onsubmit = async (event) => {
  event.preventDefault();
  const text = $('#prompt').value.trim();
  if (!text || sending || !currentUser) return;
  sending = true;
  $('#send').disabled = true;
  setChatStatus('Thinking…');
  let created = false;
  let optimistic = false;
  let chat = activeChat;
  try {
    if (!chat) {
      chat = await api('/v1/chats', {method: 'POST'});
      activeId = chat.id;
      activeChat = chat;
      created = true;
    }
    chat.messages.push({role: 'user', content: text});
    optimistic = true;
    $('#prompt').value = '';
    renderThread();
    const result = await api(`/v1/chats/${encodeURIComponent(chat.id)}/messages`, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({content: text}),
    });
    chat.messages.pop();
    optimistic = false;
    chat.messages.push(...result.messages);
    chat.title = result.title;
    await refreshChats().catch(() => {});
    if (activeId === chat.id) renderThread();
    setChatStatus('');
  } catch (error) {
    if (optimistic) chat.messages.pop();
    if (created && !chat.messages.length) {
      await fetch(`/v1/chats/${encodeURIComponent(chat.id)}`, {method: 'DELETE'}).catch(() => {});
      activeId = null;
      activeChat = null;
    }
    $('#prompt').value = text;
    render();
    setChatStatus(error.message || 'Could not send your message.', true);
    if (error.message?.includes('log in')) showSession();
  } finally { sending = false; $('#send').disabled = false; }
};
showSession();
