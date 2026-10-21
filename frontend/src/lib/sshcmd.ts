/** 解析用户直接粘贴的 SSH 登录指令（与后端 app/sshcmd.py 规则一致）。 */
export type ParsedSsh = { host: string; port: number; username: string; key_path: string; password: string };

export function looksLikePath(value: string): boolean {
  return Boolean(value) && (/[\\/~.]/.test(value));
}

export function parseSshCommand(text: string): ParsedSsh {
  const out: ParsedSsh = { host: '', port: 22, username: '', key_path: '', password: '' };
  const raw = (text || '').trim().replace(/^["']|["']$/g, '');
  if (!raw) return out;

  const url = raw.match(/^ssh:\/\/(?:([^@/]+)@)?([^:/]+)(?::(\d+))?/i);
  if (url) {
    out.host = url[2] || '';
    out.username = url[1] || '';
    out.port = url[3] ? Number(url[3]) : 22;
    return out;
  }

  let tokens = raw.split(/\s+/).filter(Boolean);
  if (tokens[0]?.toLowerCase().startsWith('ssh')) tokens = tokens.slice(1);
  let target = '';
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if ((token === '-p' || token === '-P') && tokens[i + 1]) {
      out.port = Number(tokens[i + 1]) || out.port;
      i += 1;
    } else if (/^-p\d+$/.test(token)) {
      out.port = Number(token.slice(2));
    } else if (token === '-l' && tokens[i + 1]) {
      out.username = tokens[i + 1];
      i += 1;
    } else if (token === '-i' && tokens[i + 1]) {
      const value = tokens[i + 1];
      if (looksLikePath(value)) out.key_path = value;
      else out.password = value;
      i += 1;
    } else if (!token.startsWith('-') && !target) {
      target = token;
    }
  }
  if (target) {
    if (target.includes('@')) {
      const [user, host] = target.split('@');
      out.username = out.username || user;
      target = host;
    }
    if (target.split(':').length === 2 && /^\d+$/.test(target.split(':')[1])) {
      const [host, port] = target.split(':');
      out.port = Number(port);
      target = host;
    }
    out.host = target;
  }
  return out;
}
