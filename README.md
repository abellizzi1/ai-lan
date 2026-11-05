# Local AI Gateway

A self-hosted AI chat site and OpenAI-compatible API for a trusted local network. This repository contains the Python gateway source and service Dockerfile.

## Publish a service update

After editing code, build and push a new version:

```bash
docker build -t angelobellizzi/ai-gateway:x.x.x services/gateway
docker push angelobellizzi/ai-gateway:x.x.x
```

Then change the value of `newTag` under the `images` field in [ai-lan-config/overlays/lan/kustomization.yaml](https://github.com/abellizzi1/ai-lan-config/blob/main/overlays/lan/kustomization.yaml). Both hosted and Ollama overlays inherit the tag.

## Deploying

Start deployment from [cluster-config](https://github.com/abellizzi1/cluster-config). This repository contains the configurations for the cluster setup using Terraform, including the deployment of ArgoCD. See the repository's README for further instructions.

## Use

### Accessing the website

Open `http://localhost:8080` from a device on the local network and sign in with the admin username and password. The admin can create other accounts and view request history and provider-reported token usage in **Admin dashboard**. Conversations are saved in PostgreSQL per account. API requests to `/v1/chat/completions` use the login session cookie.

### Shared conversations

Open a saved conversation, choose **Participants**, and add an existing account by username. Sharing grants access to the full conversation
history and lets participants send messages. Chats remain private until shared.

Only the owner can add or remove other participants or delete the conversation for everyone. Participants can leave through **Participants**.

Only one message can be processed per conversation - sending and participant changes are disabled while the AI responds, and competing API writes receive HTTP 409 rather than being queued. Other conversations remain available. Request limits and token usage apply to the account that sends the message.

### Setting token limits

In **Admin dashboard**, the admin can optionally set an hourly token limit that applies to each account, and an optional limit for an individual account. Leave either field blank and save to clear it. If both are set, the lower limit applies. The gateway counts provider-reported tokens from the previous 60 minutes and rejects new chat and API completion requests when that total reaches the limit. Requests without provider-reported usage do not add to the total, and an in-progress request can finish above the limit. Usage falls out of the rolling window automatically.

### Blocking requests

The admin can block new chat messages and API completions for all accounts or for selected accounts in **Admin dashboard**. These switches work independently: removing the all-users block leaves individual blocks in place. Blocked users can still sign in and read saved conversations, and administrators can still use the dashboard to restore request access. Requests already in progress may finish.

### API requests

For an API request, sign in first and pass the session cookie:

```bash
curl -c /tmp/ai-gateway-cookies.txt -X POST http://<YOUR_HOST_LAN_IP>:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR_ADMIN_PASSWORD"}'
curl -b /tmp/ai-gateway-cookies.txt http://<YOUR_HOST_LAN_IP>:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Hello"}]}'
```
