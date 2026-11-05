const $ = (selector) => document.querySelector(selector);
let currentUser = null;
let chats = [];
let activeId = null;
let activeChat = null;
let sending = false;
let chatEvents = null;
let liveReady = false;
let selectionVersion = 0;
let selecting = false;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.text();
  let data = null;
  try { data = body ? JSON.parse(body) : null; } catch { /* Use the HTTP status below. */ }
  if (!response.ok) {
    const error = new Error(data?.detail || data?.error?.message || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
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
function stopChatEvents() {
  chatEvents?.close();
  chatEvents = null;
  liveReady = false;
}
function renderControls() {
  const busy = !!activeChat?.pending || sending;
  $('#send').disabled = busy || selecting || (!!activeChat && !liveReady);
  $('#delete-chat').hidden = !activeChat?.is_owner;
  $('#delete-chat').disabled = busy;
  $('#share-chat').hidden = !activeChat;
  $('#share-chat').disabled = busy;
  $('#new-chat').disabled = sending;
  $('#live-status').textContent = activeChat?.pending
    ? `${activeChat.pending.username} sent a message. Hearth AI is thinking…`
    : activeChat ? `${activeChat.members.length + 1} participant(s) · ${liveReady ? 'Live' : 'Reconnecting…'}` : '';
  renderMembers();
}
function watchChat(id) {
  stopChatEvents();
  const source = new EventSource(`/v1/chats/${encodeURIComponent(id)}/events`);
  chatEvents = source;
  source.addEventListener('snapshot', (event) => {
    if (chatEvents !== source || activeId !== id) return;
    const chat = JSON.parse(event.data);
    const changed = JSON.stringify(activeChat) !== JSON.stringify(chat);
    activeChat = chat;
    liveReady = true;
    if (changed) {
      renderThread();
      refreshChats().catch(() => {});
    }
    renderControls();
  });
  source.addEventListener('unavailable', async (event) => {
    if (chatEvents !== source) return;
    stopChatEvents();
    selectionVersion++;
    activeId = null;
    activeChat = null;
    $('#share-dialog').close();
    render();
    if (JSON.parse(event.data).status === 401) await showSession();
    else {
      setChatStatus('This conversation was deleted or your access was removed.', true);
      await refreshChats().catch(() => {});
    }
  });
  source.onerror = async () => {
    if (chatEvents !== source) return;
    liveReady = false;
    renderControls();
    // EventSource retries transport failures. Probe HTTP errors to stop retrying
    // a deleted/revoked conversation or expired login indefinitely.
    try { await api(`/v1/chats/${encodeURIComponent(id)}`); }
    catch (error) {
      if (chatEvents !== source) return;
      if (error.status === 401) { stopChatEvents(); await showSession(); }
      else if (error.status === 404) {
        stopChatEvents();
        selectionVersion++;
        activeId = null;
        activeChat = null;
        $('#share-dialog').close();
        render();
        setChatStatus('This conversation is no longer available.', true);
        refreshChats().catch(() => {});
      }
    }
  };
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
    button.textContent = `${chat.shared ? 'Shared · ' : ''}${chat.title || 'New chat'}`;
    button.title = chat.title || 'New chat';
    button.onclick = () => selectChat(chat.id);
    button.disabled = sending;
    list.append(button);
  }
}
function renderThread() {
  const thread = $('#thread');
  thread.replaceChildren();
  $('#chat-title').textContent = activeChat?.title || 'New chat';
  if (!activeChat?.messages.length && !activeChat?.pending) {
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
  const messages = [...activeChat.messages];
  if (activeChat.pending) messages.push(activeChat.pending);
  for (const message of messages) {
    const row = document.createElement('article');
    row.className = `message ${message.role}`;
    const body = document.createElement('div');
    body.className = 'message-body';
    const name = document.createElement('div');
    name.className = 'message-name';
    name.textContent = message.role === 'assistant' ? 'Hearth AI' : message.username || 'You';
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
function render() { renderList(); renderThread(); renderControls(); }

async function refreshChats() {
  const session = currentUser;
  const result = await api('/v1/chats');
  if (!session || currentUser !== session) return;
  chats = result;
  renderList();
}
async function selectChat(id) {
  if (sending) return;
  const version = ++selectionVersion;
  const session = currentUser;
  selecting = true;
  stopChatEvents();
  liveReady = false;
  renderControls();
  try {
    const chat = await api(`/v1/chats/${encodeURIComponent(id)}`);
    if (version !== selectionVersion || currentUser !== session) return;
    activeId = chat.id;
    activeChat = chat;
    $('#share-dialog').close();
    watchChat(chat.id);
    render();
    setChatStatus('');
    closeSidebar();
  } catch (error) {
    if (version !== selectionVersion || currentUser !== session) return;
    setChatStatus(error.message, true);
    if (activeChat) watchChat(activeId);
  } finally {
    if (version === selectionVersion) { selecting = false; renderControls(); }
  }
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
    stopChatEvents();
    selectionVersion++;
    selecting = false;
    currentUser = null;
    activeChat = null;
    activeId = null;
    chats = [];
    $('#share-dialog').close();
    $('#app').hidden = true;
    $('#login-screen').hidden = false;
    return;
  }
  $('#user-name').textContent = currentUser.username;
  $('#model-pill').textContent = currentUser.default_model || 'AI model';
  $('#admin-dashboard-link').hidden = !currentUser.is_admin;
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
  stopChatEvents();
  selectionVersion++;
  selecting = false;
  await fetch('/auth/logout', {method: 'POST'}).catch(() => {});
  currentUser = null;
  chats = [];
  activeId = null;
  activeChat = null;
  $('#share-dialog').close();
  closeSidebar();
  $('#app').hidden = true;
  $('#login-screen').hidden = false;
  $('#login-error').textContent = '';
};
$('#new-chat').onclick = () => {
  if (sending) return;
  stopChatEvents();
  selectionVersion++;
  selecting = false;
  $('#share-dialog').close();
  activeId = null;
  activeChat = null;
  render();
  setChatStatus('');
  closeSidebar();
  $('#prompt').focus();
};
$('#delete-chat').onclick = async () => {
  if (!activeChat?.is_owner || sending || activeChat.pending || !confirm('Delete this conversation for everyone?')) return;
  const id = activeId;
  try {
    await api(`/v1/chats/${encodeURIComponent(id)}`, {method: 'DELETE'});
    if (activeId !== id && activeId !== null) return;
    stopChatEvents();
    selectionVersion++;
    activeId = null;
    activeChat = null;
    await refreshChats();
    if (chats.length) await selectChat(chats[0].id);
    else render();
  } catch (error) { setChatStatus(error.message, true); }
};

function renderMembers() {
  const list = $('#chat-members');
  list.replaceChildren();
  $('#share-form').hidden = !activeChat?.is_owner;
  $('#share-submit').disabled = sending || !!activeChat?.pending;
  if (!activeChat) return;
  const owner = document.createElement('li');
  owner.textContent = `${activeChat.owner_username} (owner)`;
  list.append(owner);
  for (const member of activeChat.members) {
    const row = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = member.username;
    row.append(label);
    if (activeChat.is_owner || member.username === currentUser?.username) {
      const button = document.createElement('button');
      button.className = 'icon-button';
      button.textContent = activeChat.is_owner ? 'Remove' : 'Leave';
      button.disabled = sending || !!activeChat.pending;
      button.onclick = async () => {
        const id = activeId;
        button.disabled = true;
        try {
          await api(`/v1/chats/${encodeURIComponent(id)}/members/${member.id}`, {method: 'DELETE'});
          $('#share-status').textContent = 'Participant removed.';
          refreshChats().catch(() => {});
        } catch (error) { $('#share-status').textContent = error.message; }
        finally { button.disabled = false; }
      };
      row.append(button);
    }
    list.append(row);
  }
}
$('#share-chat').onclick = () => {
  $('#share-status').textContent = '';
  renderMembers();
  $('#share-dialog').showModal();
};
$('#close-share').onclick = () => $('#share-dialog').close();
$('#share-form').onsubmit = async (event) => {
  event.preventDefault();
  if (!activeChat?.is_owner || activeChat.pending || sending) return;
  const id = activeId;
  $('#share-submit').disabled = true;
  try {
    await api(`/v1/chats/${encodeURIComponent(id)}/members`, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({username: $('#share-username').value}),
    });
    $('#share-username').value = '';
    $('#share-status').textContent = 'Account added. The conversation will appear in their sidebar.';
    refreshChats().catch(() => {});
  } catch (error) { $('#share-status').textContent = error.message; }
  finally { renderControls(); }
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
  if (!text || sending || selecting || !currentUser || activeChat?.pending || (activeChat && !liveReady)) return;
  const session = currentUser;
  sending = true;
  renderList();
  renderControls();
  setChatStatus('Thinking…');
  let chat = activeChat;
  try {
    if (!chat) {
      chat = await api('/v1/chats', {method: 'POST'});
      if (currentUser !== session) return;
      activeId = chat.id;
      activeChat = chat;
      watchChat(chat.id);
    }
    $('#prompt').value = '';
    await api(`/v1/chats/${encodeURIComponent(chat.id)}/messages`, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({content: text}),
    });
    if (currentUser !== session) return;
    await refreshChats().catch(() => {});
    // The event stream supplies the canonical thread, including our own messages.
    setChatStatus('');
  } catch (error) {
    if (currentUser !== session) return;
    $('#prompt').value = text;
    render();
    setChatStatus(error.message || 'Could not send your message.', true);
    if (error.status === 401) await showSession();
  } finally {
    sending = false;
    renderList();
    renderControls();
  }
};
// Discover invitations and sidebar title changes without another permanent stream.
setInterval(() => {
  if (currentUser && !document.hidden) refreshChats().catch(() => {});
}, 15000);
document.addEventListener('visibilitychange', () => {
  if (currentUser && !document.hidden) refreshChats().catch(() => {});
});
showSession();
