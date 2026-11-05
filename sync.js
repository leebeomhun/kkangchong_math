/* Persistent operation journal: retries and concurrent devices never add an event twice. */
(() => {
  'use strict';
  const META = 'kkangchong-sync-v1';
  const EVENT = 'kkangchong-sync-event-v1:';
  const BACKUP = 'kkangchong-before-sync-v1';
  const RECOVERY = 'kkangchong-sync-recovery-v1';
  const clone = value => JSON.parse(JSON.stringify(value));
  const id = () => crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');

  function create({ initialState, defaults, cleanSettings, onChange, onStatus }) {
    const storage = localStorage, memory = new Map();
    let storageFailed = false, stopped = false, busy = false, timer = 0, failures = 0;
    let meta = read(META);
    const firstVisit = !meta || !meta.base || !meta.base.settings || !Number.isInteger(meta.revision);
    if (firstVisit) {
      meta = { epoch: null, revision: -1, base: clone(defaults), updatedAt: null };
      if (!read(BACKUP)) write(BACKUP, initialState);
      const event = { id: id(), type: 'legacy', snapshot: clone(initialState) };
      put({ event, epoch: null, after: null, order: Date.now() });
      write(META, meta);
    }
    let lastSettings = clone(initialState.settings);

    function read(key) {
      try { return JSON.parse(storage.getItem(key) || 'null'); } catch (_) { return null; }
    }
    function write(key, value) {
      try { storage.setItem(key, JSON.stringify(value)); return true; }
      catch (_) { storageFailed = true; return false; }
    }
    function put(entry) {
      memory.set(entry.event.id, entry);
      write(EVENT + entry.event.id, entry);
    }
    function remove(entry) {
      memory.delete(entry.event.id);
      try { storage.removeItem(EVENT + entry.event.id); } catch (_) { storageFailed = true; }
    }
    function entries() {
      const all = new Map(memory);
      try {
        for (let i = 0; i < storage.length; i++) {
          const key = storage.key(i);
          if (!key || !key.startsWith(EVENT)) continue;
          const entry = read(key);
          if (entry && entry.event && typeof entry.event.id === 'string') all.set(entry.event.id, entry);
        }
      } catch (_) { storageFailed = true; }
      return [...all.values()].sort((a, b) => a.order - b.order || a.event.id.localeCompare(b.event.id));
    }
    function refreshMeta() {
      const saved = read(META);
      if (saved && saved.base && saved.revision > meta.revision) meta = saved;
    }
    function backup(reason, pending) {
      if (!pending.length) return;
      const old = read(RECOVERY);
      const list = Array.isArray(old) ? old.slice(-2) : [];
      list.push({ reason, savedAt: new Date().toISOString(), base: meta.base, pending });
      write(RECOVERY, list);
    }
    function apply(state, event) {
      if (event.type === 'legacy') {
        const other = event.snapshot;
        for (const key of ['stars', 'rounds']) state[key] = Math.max(state[key], +other[key] || 0);
        for (const key of ['stickers', 'missed']) {
          for (const [name, count] of Object.entries(other[key] || {})) state[key][name] = Math.max(state[key][name] || 0, count);
        }
        for (const [day, row] of Object.entries(other.log || {})) {
          const target = state.log[day] ||= { solved: 0, first: 0, rounds: 0 };
          for (const key of ['solved', 'first', 'rounds']) target[key] = Math.max(target[key], row[key] || 0);
        }
        if (meta.revision < 1) state.settings = cleanSettings(other.settings);
      } else if (event.type === 'settings') {
        state.settings = cleanSettings({ ...state.settings, ...event.patch });
      } else if (event.type === 'reset') {
        const settings = state.settings;
        Object.assign(state, clone(defaults), { settings });
      } else if (event.type === 'sticker') {
        state.stickers[event.sticker] = (state.stickers[event.sticker] || 0) + 1;
      } else {
        const row = state.log[event.day] ||= { solved: 0, first: 0, rounds: 0 };
        if (event.type === 'round') { state.rounds++; row.rounds++; }
        if (event.type === 'answer') {
          state.stars++; row.solved++; if (event.first) row.first++;
          const key = `${event.a}+${event.b}`;
          if (event.first) {
            if ((state.missed[key] || 0) > 1) state.missed[key]--;
            else delete state.missed[key];
          } else state.missed[key] = Math.min(3, (state.missed[key] || 0) + 1);
        }
      }
      Object.keys(state.log).sort().slice(0, -60).forEach(day => { delete state.log[day]; });
    }
    function publish() {
      refreshMeta();
      const view = clone(meta.base), pending = entries();
      const resets = new Set(pending.filter(e => e.event.type === 'reset').map(e => e.event.id));
      for (const entry of pending) {
        if (entry.epoch === null || entry.epoch === meta.epoch || (entry.after && resets.has(entry.after))) apply(view, entry.event);
      }
      lastSettings = clone(view.settings);
      onChange(view);
    }
    function status(kind) {
      onStatus(storageFailed ? 'storage' : kind, { pending: entries().length, updatedAt: meta.updatedAt });
    }
    function schedule(delay = 250) {
      clearTimeout(timer);
      if (!stopped) timer = setTimeout(flush, delay);
    }
    function capture(view, event) {
      refreshMeta();
      let pending = entries();
      if (event && event.type === 'reset') {
        backup('local-reset', pending);
        pending.forEach(remove);
        pending = [];
      }
      let after = pending.filter(e => e.event.type === 'reset').at(-1)?.event.id || null;
      const add = data => {
        const order = entries().reduce((value, e) => Math.max(value, e.order + 1), Date.now());
        const entry = { event: { ...data, id: id() }, epoch: meta.epoch, after, order };
        put(entry);
        if (data.type === 'reset') after = entry.event.id;
      };
      const patch = {};
      for (const [key, value] of Object.entries(view.settings)) {
        if (JSON.stringify(value) !== JSON.stringify(lastSettings[key])) patch[key] = clone(value);
      }
      if (Object.keys(patch).length) add({ type: 'settings', patch });
      if (event) add(event);
      publish(); status('saving'); schedule();
    }
    function accept(data, acknowledged = []) {
      if (!data || typeof data.epoch !== 'string' || !Number.isInteger(data.revision) || !data.state?.settings) throw new Error('Invalid sync response');
      refreshMeta();
      acknowledged.forEach(remove);
      if (data.revision < meta.revision) { publish(); return false; }
      let pending = entries();
      // A reset whose response was lost must still preserve work done after that reset.
      const reset = pending.find(e => e.event.type === 'reset' && e.event.id === data.resetId);
      if (reset) { remove(reset); pending = entries(); }
      for (const entry of pending) {
        if (entry.after && entry.after === data.resetId) {
          entry.after = null; entry.epoch = data.epoch; put(entry);
        }
      }
      pending = entries();
      const pendingResetIds = new Set(pending.filter(e => e.event.type === 'reset').map(e => e.event.id));
      const wrongGeneration = e => e.epoch === null ? !!data.resetId : e.epoch !== data.epoch;
      const staleResetIds = new Set(pending.filter(e => e.event.type === 'reset' && !e.after && wrongGeneration(e)).map(e => e.event.id));
      const stale = pending.filter(e =>
        (e.event.type === 'legacy' && !data.legacyAllowed) ||
        (e.after ? !pendingResetIds.has(e.after) || staleResetIds.has(e.after) : wrongGeneration(e)));
      backup('server-reset', stale); stale.forEach(remove);
      if (data.revision >= meta.revision) {
        meta = { epoch: data.epoch, revision: data.revision, base: data.state, updatedAt: data.updatedAt };
        write(META, meta);
      }
      publish();
      return stale.length > 0;
    }
    async function request(body) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const payload = body ? JSON.stringify(body) : undefined;
        const response = await fetch('/api/sync', {
          method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
          headers: body ? { 'Content-Type': 'application/json' } : {},
          body: payload, signal: controller.signal, keepalive: !!payload && payload.length < 50000
        });
        if (response.status === 403) throw Object.assign(new Error('Login required'), { auth: true });
        if (response.redirected || (!response.ok && response.status !== 409) || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Sync unavailable');
        return { data: await response.json(), conflict: response.status === 409 };
      } finally { clearTimeout(timeout); }
    }
    async function flush() {
      if (busy || stopped) return;
      busy = true;
      clearTimeout(timer);
      try {
        refreshMeta();
        let resetNotice = false;
        if (!meta.epoch) {
          const { data } = await request();
          resetNotice = accept(data);
        }
        let pending = entries().filter(e => !e.after && (e.epoch === null || e.epoch === meta.epoch));
        if (pending.length) {
          status('saving');
          const resetIndex = pending.findIndex(e => e.event.type === 'reset');
          if (resetIndex >= 0) pending = pending.slice(0, resetIndex + 1);
          pending = pending.slice(0, 100);
          const { data, conflict } = await request({ epoch: meta.epoch, events: pending.map(e => e.event) });
          resetNotice = accept(data, conflict ? [] : pending) || resetNotice;
        } else {
          const { data } = await request();
          resetNotice = accept(data) || resetNotice;
        }
        failures = 0;
        status(resetNotice ? 'reset' : entries().length ? 'saving' : 'saved');
        schedule(entries().length ? 100 : 15000);
      } catch (error) {
        status(error.auth ? 'auth' : 'offline');
        failures++;
        schedule(Math.min(30000, 2000 * 2 ** Math.min(failures - 1, 4)));
      } finally { busy = false; }
    }
    function wake() { if (!document.hidden) schedule(0); }
    addEventListener('online', wake);
    addEventListener('focus', wake);
    document.addEventListener('visibilitychange', wake);
    addEventListener('storage', event => {
      if (event.key === META || event.key?.startsWith(EVENT)) {
        // Removed receipts must not survive in this tab's in-memory fallback.
        if (event.key?.startsWith(EVENT) && event.newValue === null) memory.delete(event.key.slice(EVENT.length));
        publish(); schedule(200);
      }
    });
    addEventListener('pagehide', () => { if (entries().length) flush(); });
    publish(); status('saving'); schedule(0);
    return { capture, flush, stop() { stopped = true; clearTimeout(timer); } };
  }
  window.KkangchongSync = { create };
})();
