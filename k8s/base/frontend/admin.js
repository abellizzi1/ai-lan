const $ = (selector) => document.querySelector(selector);
let users = [];
let globalTokenLimit = null;
let selectedUserId = null;
let selectedConversationId = null;
let selectionVersion = 0;
let conversationVersion = 0;
let conversations = [];
let requests = [];
let nextConversationOffset = null;
let nextRequestOffset = null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.text();
  let data = null;
  try { data = body ? JSON.parse(body) : null; } catch { /* HTTP status is reported below. */ }
  if (!response.ok) throw new Error(data?.detail || `Request failed (${response.status}).`);
  return data;
}

const number = (value) => Number(value || 0).toLocaleString();
const count = (value) => value == null ? '—' : number(value);
const time = (value) => value ? new Date(value).toLocaleString() : '—';

function limitFromInput(input) {
  const value = input.value.trim();
  if (!value) return null;
  const limit = Number(value);
  if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(limit)) {
    throw new Error('Enter a positive whole number of tokens, or leave the field blank.');
  }
  return limit;
}

function limitRequest(path, tokenLimit) {
  return api(path, {
    method: 'PUT',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({token_limit: tokenLimit}),
  });
}

function makeMetric(label, value) {
  const card = document.createElement('div');
  card.className = 'metric';
  const caption = document.createElement('span');
  caption.textContent = label;
  const total = document.createElement('strong');
  total.textContent = value;
  card.append(caption, total);
  return card;
}

function showEmpty(container, message, columns = 1) {
  container.replaceChildren();
  if (container.tagName === 'TBODY') {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = columns;
    cell.className = 'empty-row';
    cell.textContent = message;
    row.append(cell);
    container.append(row);
  } else {
    const empty = document.createElement('p');
    empty.className = 'empty-row';
    empty.textContent = message;
    container.append(empty);
  }
}

function appendRow(container, values, numericColumns = []) {
  const row = document.createElement('tr');
  values.forEach((value, index) => {
    const cell = document.createElement('td');
    cell.textContent = String(value);
    if (numericColumns.includes(index)) cell.className = 'number';
    row.append(cell);
  });
  container.append(row);
}

function renderOverview() {
  const overview = $('#overview');
  overview.replaceChildren(
    makeMetric('Accounts', number(users.length)),
    makeMetric('Charged requests', number(users.reduce((sum, user) => sum + user.charged_requests, 0))),
    makeMetric('All-time measured tokens', number(users.reduce((sum, user) => sum + user.total_tokens, 0))),
    makeMetric('Saved conversations', number(users.reduce((sum, user) => sum + user.conversation_count, 0))),
  );
}

function renderUsers() {
  const list = $('#user-list');
  list.replaceChildren();
  if (!users.length) return showEmpty(list, 'No accounts found.');
  for (const user of users) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `user-button${user.id === selectedUserId ? ' active' : ''}`;
    const name = document.createElement('strong');
    name.textContent = user.username;
    const detail = document.createElement('small');
    detail.textContent = `${number(user.charged_requests)} requests · ${number(user.total_tokens)} tokens`;
    button.append(name, detail);
    button.onclick = () => selectUser(user.id);
    list.append(button);
  }
}

function renderUserSummary(user) {
  $('#detail-empty').hidden = true;
  $('#detail-content').hidden = false;
  $('#detail-name').textContent = user.username;
  $('#detail-meta').textContent = `Created ${time(user.created_at)} · ${number(user.active_sessions)} active session(s) · ${number(user.message_count)} saved messages`;
  $('#user-token-limit').value = user.token_limit ?? '';
  $('#hourly-token-usage').textContent = `${number(user.hourly_tokens)} provider-reported tokens in the last 60 minutes.`;
  $('#user-limit-status').textContent = '';
  $('#user-limit-status').classList.remove('error');
  const badge = $('#detail-badge');
  badge.textContent = user.disabled ? 'Disabled' : user.is_admin ? 'Administrator' : 'Member';
  badge.classList.toggle('disabled', user.disabled);
  $('#user-metrics').replaceChildren(
    makeMetric('Charged requests', number(user.charged_requests)),
    makeMetric('Logged / measured', `${number(user.logged_requests)} / ${number(user.measured_requests)}`),
    makeMetric('Input / output tokens', `${number(user.prompt_tokens)} / ${number(user.completion_tokens)}`),
    makeMetric('All-time tokens', number(user.total_tokens)),
  );
}

function renderUsage(usage) {
  const quota = $('#request-days');
  quota.replaceChildren();
  if (!usage.request_days.length) showEmpty(quota, 'No charged requests.', 2);
  for (const day of usage.request_days) appendRow(quota, [day.usage_date, number(day.requests)], [1]);

  const tokens = $('#token-days');
  tokens.replaceChildren();
  if (!usage.token_days.length) showEmpty(tokens, 'No request logs.', 5);
  for (const day of usage.token_days) {
    appendRow(tokens, [day.usage_date,
      `${number(day.measured_requests)} / ${number(day.logged_requests)}`,
      number(day.prompt_tokens), number(day.completion_tokens), number(day.total_tokens)], [1, 2, 3, 4]);
  }

  const models = $('#model-usage');
  models.replaceChildren();
  if (!usage.models.length) showEmpty(models, 'No request logs.', 5);
  for (const model of usage.models) {
    appendRow(models, [model.model || 'Unknown model',
      `${number(model.measured_requests)} / ${number(model.logged_requests)}`,
      number(model.prompt_tokens), number(model.completion_tokens), number(model.total_tokens)], [1, 2, 3, 4]);
  }
}

function renderConversations() {
  const list = $('#conversation-list');
  list.replaceChildren();
  if (!conversations.length) showEmpty(list, 'No saved conversations.');
  for (const chat of conversations) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `conversation-button${chat.id === selectedConversationId ? ' active' : ''}`;
    const title = document.createElement('strong');
    title.textContent = chat.title || 'New chat';
    const detail = document.createElement('small');
    detail.textContent = `${number(chat.message_count)} messages · updated ${time(chat.updated_at)}`;
    button.append(title, detail);
    button.onclick = () => openConversation(chat.id);
    list.append(button);
  }
  $('#more-conversations').hidden = nextConversationOffset == null;
}

function renderRequests() {
  const table = $('#request-history');
  table.replaceChildren();
  if (!requests.length) showEmpty(table, 'No logged requests.', 7);
  for (const item of requests) {
    appendRow(table, [time(item.created_at), item.model || '—', item.status_code,
      item.client_ip || '—', count(item.prompt_tokens), count(item.completion_tokens),
      count(item.total_tokens)], [4, 5, 6]);
  }
  $('#more-requests').hidden = nextRequestOffset == null;
}

async function selectUser(id) {
  const user = users.find((item) => item.id === id);
  if (!user) return;
  const version = ++selectionVersion;
  ++conversationVersion;
  selectedUserId = id;
  selectedConversationId = null;
  conversations = [];
  requests = [];
  nextConversationOffset = null;
  nextRequestOffset = null;
  $('#conversation-view').hidden = true;
  $('#detail-status').textContent = 'Loading user data…';
  renderUsers();
  renderUserSummary(user);
  showEmpty($('#request-days'), 'Loading…', 2);
  showEmpty($('#token-days'), 'Loading…', 5);
  showEmpty($('#model-usage'), 'Loading…', 5);
  showEmpty($('#conversation-list'), 'Loading…');
  showEmpty($('#request-history'), 'Loading…', 7);
  $('#more-conversations').hidden = true;
  $('#more-requests').hidden = true;
  try {
    const [usage, chatsPage, requestsPage] = await Promise.all([
      api(`/auth/admin/users/${id}/usage`),
      api(`/auth/admin/users/${id}/conversations`),
      api(`/auth/admin/users/${id}/requests`),
    ]);
    if (version !== selectionVersion) return;
    conversations = chatsPage.items;
    requests = requestsPage.items;
    nextConversationOffset = chatsPage.next_offset;
    nextRequestOffset = requestsPage.next_offset;
    renderUsage(usage);
    renderConversations();
    renderRequests();
    $('#detail-status').textContent = '';
  } catch (error) {
    if (version === selectionVersion) $('#detail-status').textContent = error.message;
  }
}

async function openConversation(id) {
  const version = ++conversationVersion;
  const ownerId = selectedUserId;
  selectedConversationId = id;
  renderConversations();
  $('#conversation-view').hidden = false;
  $('#conversation-title').textContent = 'Loading conversation…';
  $('#conversation-meta').textContent = '';
  $('#message-list').replaceChildren();
  try {
    const data = await api(`/auth/admin/conversations/${encodeURIComponent(id)}`);
    if (version !== conversationVersion || ownerId !== selectedUserId) return;
    const chat = data.conversation;
    $('#conversation-title').textContent = chat.title || 'New chat';
    $('#conversation-meta').textContent = `Created ${time(chat.created_at)} · updated ${time(chat.updated_at)}${chat.source_id ? ` · imported source ${chat.source_id}` : ''}`;
    const list = $('#message-list');
    list.replaceChildren();
    if (!data.messages.length) return showEmpty(list, 'No messages in this conversation.');
    for (const message of data.messages) {
      const article = document.createElement('article');
      article.className = 'message';
      const head = document.createElement('div');
      head.className = 'message-head';
      const role = document.createElement('strong');
      role.textContent = message.role === 'assistant' ? 'Assistant' : 'User';
      const stamp = document.createElement('span');
      stamp.textContent = time(message.created_at);
      head.append(role, stamp);
      const content = document.createElement('div');
      content.className = 'message-content';
      content.textContent = message.content;
      article.append(head, content);
      list.append(article);
    }
  } catch (error) {
    if (version === conversationVersion) $('#conversation-title').textContent = error.message;
  }
}

async function loadMore(kind) {
  const isChat = kind === 'conversations';
  const offset = isChat ? nextConversationOffset : nextRequestOffset;
  if (offset == null || selectedUserId == null) return;
  const button = isChat ? $('#more-conversations') : $('#more-requests');
  const ownerId = selectedUserId;
  const version = selectionVersion;
  button.disabled = true;
  try {
    const page = await api(`/auth/admin/users/${ownerId}/${kind}?offset=${offset}`);
    if (version !== selectionVersion) return;
    if (isChat) {
      conversations.push(...page.items);
      nextConversationOffset = page.next_offset;
      renderConversations();
    } else {
      requests.push(...page.items);
      nextRequestOffset = page.next_offset;
      renderRequests();
    }
  } catch (error) {
    if (version === selectionVersion) $('#detail-status').textContent = error.message;
  } finally { button.disabled = false; }
}

async function refresh() {
  $('#refresh').disabled = true;
  $('#page-status').textContent = '';
  try {
    [users, {token_limit: globalTokenLimit}] = await Promise.all([
      api('/auth/admin/users'), api('/auth/admin/token-limit'),
    ]);
    $('#global-token-limit').value = globalTokenLimit ?? '';
    $('#admin-content').hidden = false;
    renderOverview();
    const id = users.some((user) => user.id === selectedUserId) ? selectedUserId : users[0]?.id;
    if (id != null) await selectUser(id);
    else {
      selectedUserId = null;
      $('#detail-empty').hidden = false;
      $('#detail-content').hidden = true;
      renderUsers();
    }
  } catch (error) { $('#page-status').textContent = error.message; }
  finally { $('#refresh').disabled = false; }
}

$('#more-conversations').onclick = () => loadMore('conversations');
$('#more-requests').onclick = () => loadMore('requests');
$('#refresh').onclick = refresh;
$('#global-limit-form').onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const status = $('#global-limit-status');
  button.disabled = true;
  status.textContent = '';
  status.classList.remove('error');
  try {
    const limit = limitFromInput($('#global-token-limit'));
    const result = await limitRequest('/auth/admin/token-limit', limit);
    globalTokenLimit = result.token_limit;
    status.textContent = result.token_limit == null ? 'All users limit cleared.' : 'All users limit saved.';
  } catch (error) { status.textContent = error.message; status.classList.add('error'); }
  finally { button.disabled = false; }
};
$('#user-limit-form').onsubmit = async (event) => {
  event.preventDefault();
  const userId = selectedUserId;
  if (userId == null) return;
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const status = $('#user-limit-status');
  button.disabled = true;
  status.textContent = '';
  status.classList.remove('error');
  try {
    const limit = limitFromInput($('#user-token-limit'));
    const result = await limitRequest(`/auth/admin/users/${userId}/token-limit`, limit);
    const user = users.find((item) => item.id === userId);
    if (user) user.token_limit = result.token_limit;
    if (selectedUserId === userId) status.textContent = result.token_limit == null ? 'Account limit cleared.' : 'Account limit saved.';
  } catch (error) {
    if (selectedUserId === userId) { status.textContent = error.message; status.classList.add('error'); }
  }
  finally { button.disabled = false; }
};
$('#sign-out').onclick = async () => {
  await fetch('/auth/logout', {method: 'POST'}).catch(() => {});
  location.replace('/');
};

(async () => {
  try {
    const session = await api('/auth/me');
    if (!session.is_admin) return location.replace('/');
    $('#admin-name').textContent = session.username;
    await refresh();
  } catch (error) { $('#page-status').textContent = `${error.message} Sign in from the chat page.`; }
})();
