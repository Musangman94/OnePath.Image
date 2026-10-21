# Frontend model configuration

The Models view (`#models`, shortcut 5) is the configuration surface for the future backend. No provider calls, discovery, model downloads, loading, offloading or secret storage are implemented here. Workspace tasks remain demonstrations.

## Configuration

`window.OnePathModels.getConfiguration()` returns a copy of the saved nonsecret settings: system mode (`local`, `cloud`, `fusion`), preferred local/cloud selection (`connectionId::modelId`), Fusion fallback permission, agent preferences and connections. Connections contain ID, name, location, protocol, base URL, model IDs and an optional backend credential reference. API formats are adapter targets, not implemented clients.

`window.OnePathModels.resolve(agentName)` returns `{connectionId, model, kind}` or `null`. Only confirmed connections with known models are eligible. The explicit preferred model wins; otherwise the first available model in connection/model order is chosen. This is deterministic availability selection, not a claim of task-quality optimisation. Local mode blocks cloud, including Gandalf. Gandalf is currently designated cloud only. Other agents can follow the system or prefer local/cloud. In Fusion with system routing, local comes first and cloud fallback requires the saved permission. A future backend must enforce these rules, data permissions, agent capabilities and actual availability before execution.

`onepath:model-config-change` carries the configuration as `detail`; it also synchronises the Home header mode. `onepath:execution-mode` carries `{mode}` from the header back to Models. `onepath:model-state-change` signals that the Agents view should refresh connection information.

## Backend adapter boundary

Listen for `onepath:model-request` with detail:

```js
{ requestId, operation: 'connect' | 'discover', connection, configuration, apiKey? }
```

The optional key is temporary memory only and deliberately absent from localStorage, exports and the public configuration getter. An adapter must hand credentials to its authenticated secure backend and never log them. The preview does not transmit the key. Any future adapter needs its own request authentication and server-side secret handling; DOM events are an integration boundary, not a security boundary.

Respond within the preview's eight-second request window with:

```js
window.dispatchEvent(new CustomEvent('onepath:model-response', {
  detail: { requestId, ok: true, models: ['actual-model-id'] }
}));
```

Only a successful `connect` response marks a connection connected. Discovery alone does not. Failure uses `ok:false`. Unknown, expired, edited or removed connection requests are ignored. Connection verification resets on reload. Do not report success until the backend actually verifies the provider/server.

To report a connection lost after validation:

```js
window.dispatchEvent(new CustomEvent('onepath:model-status', {
  detail: { connectionId, state: 'disconnected' } // or 'error'
}));
```

The satellite shows cloud linked only when at least one cloud connection is currently confirmed; this does not indicate model activity. Click it to open Models. Reconnect through a new connect request.

## Personal space and keyboard

`#personal` (4) stores the profile and dated plans in `onepath.personal.v1`. Calendar dates are local calendar dates, not UTC timestamps; calendar arrows move 1 day horizontally or 7 days vertically. Saving the profile updates the Home greeting through the existing preference handler. No calendar service or cross-device sync exists yet.

1 Home, 2 Workspace, 3 Agents, 4 Your space, 5 Models. C/A open navigation/agent selection; arrows move focus in those menus and Enter/Space activates. Horizontal arrows change tabs. Numeric shortcuts ignore text entry, selects, composition, modifier chords and repeated keys. Appearance is at the bottom right and retains T. Reduced-motion preferences stop satellite and icon animation.
