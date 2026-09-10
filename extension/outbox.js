(function (root) {
  'use strict';
  class BrowserOutbox {
    constructor(name = 'soft-tracking-v3') {
      this.database = new Promise((resolve, reject) => {
        const request = indexedDB.open(name, 2);
        request.onupgradeneeded = () => {
          const db = request.result;
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
        const tx = db.transaction('events', mode);
        let result;
        tx.oncomplete = () => resolve(result);
        tx.onabort = tx.onerror = () => reject(tx.error || new Error('Outbox transaction failed'));
        fn(tx.objectStore('events'), value => { result = value; }, tx);
      });
    }
    put(event) {
      return this.transaction('readwrite', (store, result, tx) => {
        const query = store.get(event.event_id);
        query.onsuccess = () => {
          if (query.result) {
            if (JSON.stringify(query.result.event) !== JSON.stringify(event)) tx.abort();
          } else store.add({event_id: event.event_id, event, state: 'pending'});
        };
      });
    }
    batch() {
      return this.transaction('readonly', (store, result) => {
        const query = store.index('state').getAll('pending', 100);
        query.onsuccess = () => result(query.result.map(row => row.event));
      });
    }
    acknowledge(ids, rejected = {}) {
      return this.transaction('readwrite', store => {
        ids.forEach(id => store.delete(id));
        Object.entries(rejected).forEach(([id, error]) => {
          const query = store.get(id);
          query.onsuccess = () => {
            if (query.result) store.put(Object.assign({}, query.result, {state: 'rejected', error}));
          };
        });
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
