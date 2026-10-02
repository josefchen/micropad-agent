const vscode = require("vscode");
const fs = require("fs");
const os = require("os");
const path = require("path");

const DIR = path.join(os.homedir(), ".cache", "micropad");
const REQUEST = path.join(DIR, "cursor-open.json");
const MAX_AGE_MS = 5000;

let lastHandled = 0;

function ownsWorkspace(workspace) {
  const folders = vscode.workspace.workspaceFolders || [];
  return folders.some((f) => f.uri.fsPath === workspace);
}

async function ownsComposer(id) {
  try {
    return Boolean(await vscode.commands.executeCommand("composer.getComposerHandleById", id));
  } catch {
    return false;
  }
}

async function openComposer(id) {
  try {
    await vscode.commands.executeCommand("composer.openComposer", id);
  } catch {
    await vscode.commands.executeCommand("canvas.openAgentByConversationId", id);
  }
}

async function handleRequest() {
  let req;
  try {
    req = JSON.parse(fs.readFileSync(REQUEST, "utf8"));
  } catch {
    return;
  }
  if (!req.id || !req.ts || req.ts <= lastHandled || Date.now() - req.ts > MAX_AGE_MS) return;
  const mine = req.workspace ? ownsWorkspace(req.workspace) : await ownsComposer(req.id);
  if (!mine) return;
  lastHandled = req.ts;
  await openComposer(req.id);
}

function activate(context) {
  fs.mkdirSync(DIR, { recursive: true });
  const watcher = fs.watch(DIR, (_event, name) => {
    if (name === path.basename(REQUEST)) handleRequest();
  });
  context.subscriptions.push({ dispose: () => watcher.close() });

  context.subscriptions.push(
    vscode.window.registerUriHandler({
      handleUri(uri) {
        const id = new URLSearchParams(uri.query).get("id");
        if (id) openComposer(id);
      },
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
