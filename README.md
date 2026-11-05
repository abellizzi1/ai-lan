# Local AI Gateway

A self-hosted AI chat site and OpenAI-compatible API for a trusted local network. This repository contains the Python gateway source and service Dockerfile.

Start deployment from [cluster-config](https://github.com/abellizzi1/cluster-config). That repository owns the infrastructure runner, Terraform, cluster setup, Argo CD registrations, runtime secrets, and backup/restore scripts. The source repository is needed when changing and publishing gateway code.

| Repository | Purpose |
| --- | --- |
| [cluster-config](https://github.com/abellizzi1/cluster-config) | Starting point for cluster creation and operations. |
| [ai-lan-config](https://github.com/abellizzi1/ai-lan-config) | AI manifests, frontend files, provider/model settings, and deployed image tag. |
| This repository | `services/gateway/app/main.py`, Python dependencies, and the service Dockerfile. |

## Publish a service update

After editing service code, manually build and push a new version from this repository root:

```bash
docker build -t angelobellizzi/ai-gateway:0.0.2 services/gateway
docker push angelobellizzi/ai-gateway:0.0.2
```

Then change `images[0].newTag` in the sibling `ai-lan-config/overlays/lan/kustomization.yaml`, commit, and push that configuration repo. Both hosted and Ollama overlays inherit the tag. Argo CD deploys the update automatically. Use a new immutable image tag for each build.

Frontend files now live in `ai-lan-config/base/frontend/`; edit and push them there. Generated ConfigMap hashes roll affected pods.

Infrastructure and ignored runtime files formerly located here have moved to `cluster-config`. Keep Terraform state and database backups with that repository before operating an existing cluster. See its README for migration and setup instructions.

## Use

### Accessing the website

Open `http://<YOUR_HOST_LAN_IP>:8080` from a device on the local network and sign in with the admin username and password. The admin can create other accounts and view request history and provider-reported token usage in **Admin dashboard**. Conversations are saved in PostgreSQL per account. API requests to `/v1/chat/completions` use the login session cookie.

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
