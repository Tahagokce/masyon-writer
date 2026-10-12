export class SecretGuard {
  constructor(names = (process.env.SECRET_ENV_NAMES || 'WBS_USERNAME,WBS_PASSWORD').split(',')) {
    if (names.some(n => !/^[A-Z][A-Z0-9_]*$/.test(n))) throw new Error('Invalid credential names');
    this.values = Object.fromEntries(names.filter(n => process.env[n]).map(n => [n, process.env[n]]));
    this.variants = new Set(Object.values(this.values).flatMap(v => [v, JSON.stringify(v).slice(1, -1),
      encodeURIComponent(v), Buffer.from(v).toString('base64')]));
  }
  clean(text) {
    for (const secret of [...this.variants].sort((a,b) => b.length-a.length)) text = text.split(secret).join('[REDACTED]');
    return text.replace(/(https?:\/\/)[^\s/]+:[^\s/@]+@/gi, '$1[REDACTED]@')
      .replace(/(bearer\s+)[\w.~/+=-]+/gi, '$1[REDACTED]')
      .replace(/([?&](?:[^=\s]*(?:token|secret|key|session|auth)[^=\s]*)=)[^&#\s'"]+/gi, '$1[REDACTED]')
      .replace(/(["']?(?:password|authorization|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?(?:id|secret))["']?\s*[:=]\s*)(["'])(.*?)\2/gi, '$1$2[REDACTED]$2')
      .replace(/((?:token|api[_-]?key|password|authorization|cookie)\s*[=:]\s*)[^\s,;]+/gi, '$1[REDACTED]');
  }
  diagnostic(error, tool, page) {
    // Keep the actual first diagnostic, not raw stacks, DOM dumps or arguments.
    const message = String(error?.message || error || 'Operation failed').split(/\n(?:Call log:|\s+at |\s*\d+\))/)[0];
    return {tool, errorClass:error?.name || 'Error', error:this.clean(message).slice(0,700), activePage:page};
  }
  object(value) {
    if (typeof value === 'string') return this.clean(value);
    if (Array.isArray(value)) return value.map(v => this.object(v));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,this.object(v)]));
    return value;
  }
  hasSecret(text) { return [...this.variants].some(v => text.includes(v)); }
  assertCode(code) {
    if (this.clean(code) !== code || code.includes('<secret>') || code.includes('[REDACTED]'))
      throw new Error('Recording contains secret-bearing content; export refused');
    return code;
  }
}
