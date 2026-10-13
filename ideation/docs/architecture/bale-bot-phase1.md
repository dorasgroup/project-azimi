# Bale bot integration: inactive phase 1

Status: phase-1 implementation complete. Phase-2 activation requires all checks below.

## Scope

The integration reuses the production `ChatService` and adds a Bale transport adapter.
It does not create a second RAG path. Only categories explicitly marked
`available_in_bale=true` and containing at least one active indexed document are shown.

The webhook accepts private-chat text messages and inline-keyboard callbacks. A durable
RabbitMQ queue keeps model latency outside the webhook request. `bale_updates.update_id`
is the idempotency boundary, and `bale_conversations` stores one selected category and
one Dify conversation per Bale chat.

## Activation prerequisites

1. Rotate the token that was shared during setup.
2. Create a non-login integration user and configure `BALE_INTEGRATION_USER_ID`.
3. Store the bot token and webhook secret in root-owned Docker secret files.
4. Add the backend and a dedicated Bale worker to the production Compose secret mounts.
5. Disable access logging for the webhook secret and rate-limit the endpoint in both
   Nginx layers.
6. Run the migration, enable approved categories, deploy, and only then call `setWebhook`.

## Supported user flow

- `/start`, `/categories`, or `/help`: show the available category keyboard.
- Category callback: select exactly one category and reset Dify context.
- Text: enqueue the question and answer through the shared chat service.
- `/new`: retain the selected category but reset the Dify conversation.
- Non-text messages and non-private chats: fail closed with no RAG access.

The API token is never included in application errors. Bale responses are split below
the 4096-character platform limit, and every callback is acknowledged.
