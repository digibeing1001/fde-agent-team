#!/usr/bin/env node
"";
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';

function fail(message) { process.stderr.write(`${JSON.stringify({error: message})}\n`); process.exit(2); }
function read(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function write(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, {mode: 0o600});
  try { fs.chmodSync(file, 0o600); } catch {}
}
function args(argv) {
  const result = {only: []};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--confirm-create') result.confirm = true;
    else if (argv[i] === '--only') result.only.push(argv[++i]);
    else if (['--manifest', '--output', '--sdk-root'].includes(argv[i])) result[argv[i].slice(2).replace('-', '_')] = argv[++i];
    else fail(`unknown argument: ${argv[i]}`);
  }
  if (!result.manifest) fail('--manifest is required');
  result.output ||= path.resolve('.fde-local/feishu-bot-inventory.json');
  result.sdk_root ||= path.resolve('feishu-bootstrap');
  return result;
}
function cli(profile, command, secret) {
  const executable = process.platform === 'win32' ? 'lark-cli.cmd' : 'lark-cli';
  const result = spawnSync(executable, profile ? ['--profile', profile, ...command] : command, {
    input: secret, encoding: 'utf8', windowsHide: true, shell: process.platform === 'win32',
  });
  if (result.status !== 0) throw new Error(result.stderr || result.stdout || `lark-cli exited ${result.status}`);
  return result.stdout;
}

async function main() {
  const options = args(process.argv.slice(2));
  const manifest = read(path.resolve(options.manifest));
  const known = new Set(manifest.agents.map((agent) => agent.agent_id));
  for (const id of options.only) if (!known.has(id)) fail(`unknown agent: ${id}`);
  const selected = manifest.agents.filter((agent) => !options.only.length || options.only.includes(agent.agent_id));
  if (!options.confirm) {
    process.stdout.write(`${JSON.stringify({
      dry_run: true, action: 'register_feishu_agent_bots', count: selected.length,
      agents: selected.map(({agent_id, profile}) => ({agent_id, profile})),
      requires_online_confirmation_per_agent: true,
      app_secret_storage: 'lark-cli profile credential store; never inventory or stdout',
    }, null, 2)}\n`);
    return;
  }
  let lark;
  try { lark = createRequire(path.resolve(options.sdk_root, 'package.json'))('@larksuiteoapi/node-sdk'); }
  catch { fail(`Feishu SDK not installed. Run: npm --prefix "${options.sdk_root}" install`); }
  const inventory = fs.existsSync(options.output) ? read(options.output) : {
    version: '1.0.0', kind: 'fde-feishu-bot-inventory', team_id: manifest.team_id, agents: {},
  };
  for (const agent of selected) {
    if (inventory.agents[agent.agent_id]?.status === 'ready') continue;
    const created = await lark.registerApp({
      createOnly: true, source: 'fde-agent-team',
      appPreset: {name: agent.display_name || `FDE ${agent.agent_id}`, desc: `FDE project specialist: ${agent.agent_id}`},
      onQRCodeReady(info) { process.stderr.write(`[confirm:${agent.agent_id}] ${info.url} (expires in ${info.expireIn}s)\n`); },
      onStatusChange(info) { process.stderr.write(`[${agent.agent_id}] ${info.status}\n`); },
    });
    cli(null, ['profile', 'add', '--name', agent.profile, '--app-id', created.client_id, '--app-secret-stdin'], `${created.client_secret}\n`);
    const response = JSON.parse(cli(agent.profile, ['api', 'GET', '/open-apis/bot/v3/info', '--json']));
    const bot = response.bot || response.data?.bot || response.data || {};
    inventory.agents[agent.agent_id] = {
      status: 'ready', profile: agent.profile, app_id: created.client_id, open_id: bot.open_id || '',
      app_id_env: agent.app_id_env, open_id_env: agent.open_id_env,
    };
    write(options.output, inventory);
  }
  process.stdout.write(`${JSON.stringify({status: 'ready', inventory: path.resolve(options.output)}, null, 2)}\n`);
}
main().catch((error) => fail(error?.message || String(error)));
