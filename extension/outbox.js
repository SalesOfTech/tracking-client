(function (root) {
  'use strict';
  const validEpoch = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
  const validEvent = event => event && typeof event === 'object' && !Array.isArray(event) && validEpoch(event.event_id) && !('employee_epoch' in event);
  class BrowserOutbox {
    constructor(name = 'soft-tracking-v3') {
      this.database = new Promise((resolve, reject) => {
        const request = indexedDB.open(name, 3);
        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains('sessions')) db.createObjectStore('sessions', {keyPath: 'event_id'});
          const store = db.objectStoreNames.contains('events') ? request.transaction.objectStore('events') : db.createObjectStore('events', {keyPath: 'event_id'});
          if (!store.indexNames.contains('state')) store.createIndex('state', 'state');
          const cursor = store.openCursor();
          cursor.onsuccess = () => {
            const item = cursor.result;
            if (!item) return;
            if (!item.value.event) item.update({event_id: item.value.event_id, event: item.value, state: 'pending'});
            item.continue();
          };
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    }
    async transaction(mode, fn) {
      const db = await this.database;
      return new Promise((resolve, reject) => {
        const tx = db.transaction(['events', 'sessions'], mode);
        let result;
        tx.oncomplete = () => resolve(result);
        tx.onabort = tx.onerror = () => reject(tx.error || new Error('Outbox transaction failed'));
        try { fn(tx.objectStore('events'), value => { result = value; }, tx); }
        catch (error) { tx.abort(); reject(error); }
      });
    }
    put(event, employee_epoch) {
      if (!validEvent(event) || (employee_epoch !== undefined && !validEpoch(employee_epoch))) return Promise.reject(new Error('Invalid event envelope'));
      return this.transaction('readwrite', (store, result, tx) => {
        const query = store.get(event.event_id);
        query.onsuccess = () => {
          if (query.result) {
            if (JSON.stringify(query.result.event) !== JSON.stringify(event) || query.result.employee_epoch !== employee_epoch) tx.abort();
          } else store.add({event_id: event.event_id, event, employee_epoch, state: 'pending'});
        };
      });
    }
    storedEvent(event, employee_epoch) {
      if (!validEvent(event) || !validEpoch(employee_epoch)) return Promise.reject(new Error('Invalid event envelope'));
      return this.transaction('readonly', (store, result, tx) => {
        const query = store.get(event.event_id);
        query.onsuccess = () => {
          const row = query.result;
          if (!row) { result(false); return; }
          if (row.employee_epoch !== employee_epoch || JSON.stringify(row.event) !== JSON.stringify(event)) { tx.abort(); return; }
          result(true);
        };
      });
    }
    batch() {
      return this.transaction('readonly', (store, result) => {
        const query = store.index('state').getAll('pending', 100);
        query.onsuccess = () => result(query.result.map(row => row.event));
      });
    }
    checkpointSession(event, employee_epoch, close = false, existingOnly = false) {
      if (!validEpoch(employee_epoch) || !validEvent(event) || event.type !== 'web_session'
          || typeof close !== 'boolean' || typeof existingOnly !== 'boolean'
          || !Number.isInteger(event.timestamp) || !Number.isInteger(event.end_timestamp)
          || event.end_timestamp < event.timestamp || event.end_timestamp - event.timestamp > 3600
          || typeof event.url !== 'string') return Promise.reject(new Error('Invalid session envelope'));
      return this.transaction('readwrite', (store, result, tx) => {
        const sessions = tx.objectStore('sessions');
        const query = sessions.get(event.event_id);
        query.onsuccess = () => {
          const old = query.result;
          if (!old && existingOnly) { result(false); return; }
          if (old && old.employee_epoch !== employee_epoch) { tx.abort(); return; }
          if (old && old.closed) { result(true); return; }
          if (old && (old.event.timestamp !== event.timestamp || old.event.url !== event.url || old.event.end_timestamp > event.end_timestamp)) { tx.abort(); return; }
          const row = {event_id: event.event_id, event, employee_epoch, state: 'pending'};
          if (close) {
            if (event.end_timestamp > event.timestamp) store.add(row);
            sessions.put({event_id: event.event_id, employee_epoch, closed: true});
          } else sessions.put(row);
          result(true);
        };
      });
    }
    closeSession(event_id, employee_epoch) {
      if (!validEpoch(event_id) || !validEpoch(employee_epoch)) return Promise.reject(new Error('Invalid session close'));
      return this.transaction('readwrite', (store, result, tx) => {
        const sessions = tx.objectStore('sessions');
        const query = sessions.get(event_id);
        query.onsuccess = () => {
          const row = query.result;
          if (!row || row.employee_epoch !== employee_epoch) { result(false); return; }
          if (!row.closed) {
            if (row.event.end_timestamp > row.event.timestamp) store.add(row);
            sessions.put({event_id, employee_epoch, closed: true});
          }
          result(true);
        };
      });
    }
    closedSession(event_id, employee_epoch) {
      if (!validEpoch(event_id) || !validEpoch(employee_epoch)) return Promise.reject(new Error('Invalid session lookup'));
      return this.transaction('readonly', (store, result, tx) => {
        const query = tx.objectStore('sessions').get(event_id);
        query.onsuccess = () => result(Boolean(query.result && query.result.employee_epoch === employee_epoch && query.result.closed === true));
      });
    }
    transitionEpoch(legacy, current, recover = false) {
      if (!validEpoch(legacy) || !validEpoch(current) || typeof recover !== 'boolean') return Promise.reject(new Error('Missing employee epoch'));
      return this.transaction('readwrite', (store, result, tx) => {
        for (const source of [store, tx.objectStore('sessions')]) {
          const query = source.openCursor();
          query.onsuccess = () => {
            const cursor = query.result;
            if (!cursor) return;
            const row = cursor.value;
            if (row.employee_epoch == null) { row.employee_epoch = legacy; cursor.update(row); }
            if (!validEpoch(row.employee_epoch)) { tx.abort(); return; }
            if (source.name === 'sessions' && !row.closed && (recover || row.employee_epoch !== current)) {
              if (row.event.end_timestamp > row.event.timestamp) store.add(row);
              cursor.update({event_id: row.event_id, employee_epoch: row.employee_epoch, closed: true});
            }
            cursor.continue();
          };
        }
      });
    }
    epochBatches() {
      return this.transaction('readonly', (store, result, tx) => {
        const groups = new Map();
        const query = store.index('state').openCursor('pending');
        query.onsuccess = () => {
          const cursor = query.result;
          if (!cursor) { result(Array.from(groups, ([employee_epoch, events]) => ({employee_epoch, events}))); return; }
          const row = cursor.value;
          if (!validEpoch(row.employee_epoch)) { tx.abort(); return; }
          if (!groups.has(row.employee_epoch)) groups.set(row.employee_epoch, []);
          const events = groups.get(row.employee_epoch);
          if (events.length < 100) events.push(row.event);
          cursor.continue();
        };
      });
    }
    acknowledge(ids, rejected = {}, employee_epoch) {
      if (!Array.isArray(ids) || !rejected || typeof rejected !== 'object' || Array.isArray(rejected)
          || ids.some(id => !validEpoch(id)) || new Set(ids).size !== ids.length
          || Object.keys(rejected).some(id => !validEpoch(id) || ids.includes(id) || typeof rejected[id] !== 'string')
          || (employee_epoch !== undefined && !validEpoch(employee_epoch))) return Promise.reject(new Error('Invalid acknowledgement'));
      return this.transaction('readwrite', (store, result, tx) => {
        for (const id of [...ids, ...Object.keys(rejected)]) {
          const query = store.get(id);
          query.onsuccess = () => {
            if (!query.result) return;
            if (query.result.employee_epoch !== employee_epoch) { tx.abort(); return; }
            if (ids.includes(id)) store.delete(id);
            else store.put(Object.assign({}, query.result, {state: 'rejected', error: rejected[id]}));
          };
        }
      });
    }
    counts() {
      return this.transaction('readonly', (store, result) => {
        const counts = {};
        for (const state of ['pending', 'rejected']) {
          const query = store.index('state').count(state);
          query.onsuccess = () => { counts[state] = query.result; result(counts); };
        }
      });
    }
    retryRejected() {
      return this.transaction('readwrite', store => {
        const query=store.index('state').openCursor('rejected');
        query.onsuccess=()=>{
          const cursor=query.result;
          if (!cursor) return;
          cursor.update(Object.assign({},cursor.value,{state:'pending',error:null}));
          cursor.continue();
        };
      });
    }
    async close() { (await this.database).close(); }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = BrowserOutbox;
  else root.TrackingOutbox = BrowserOutbox;
})(globalThis);
