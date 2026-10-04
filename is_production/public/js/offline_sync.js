// apps/is_production/public/js/offline_sync.js

// Utility for offline caching and sync for Hourly Production
const OFFLINE_KEY = 'hourly_production_offline_cache';

function isOnline() {
    return window.navigator.onLine;
}

function getCachedDocs() {
    const data = localStorage.getItem(OFFLINE_KEY);
    return data ? JSON.parse(data) : [];
}

function cacheDoc(doc) {
    let docs = getCachedDocs();
    // Remove any previous cache for this doc (by name)
    docs = docs.filter(d => d.name !== doc.name);
    docs.push(doc);
    localStorage.setItem(OFFLINE_KEY, JSON.stringify(docs));
}

function removeCachedDoc(docName) {
    let docs = getCachedDocs();
    docs = docs.filter(d => d.name !== docName);
    localStorage.setItem(OFFLINE_KEY, JSON.stringify(docs));
}

function clearAllCachedDocs() {
    localStorage.removeItem(OFFLINE_KEY);
}

async function syncCachedDocs() {
    const docs = getCachedDocs();
    if (!docs.length) return { success: 0, failed: 0, skipped: 0 };

    const results = {
        success: 0,
        failed: 0,
        skipped: 0
    };

    for (const doc of docs) {
        // Docs cached before their first save only exist in this browser
        // (name "new-hourly-production-..."), so they must be inserted, not updated.
        // insert() replaces the temporary name with the format:{location}-{prod_date}-{hour_slot} autoname.
        const isNew = !!doc.__islocal || String(doc.name || '').startsWith('new-');

        try {
            await frappe.call({
                method: isNew ? 'frappe.client.insert' : 'frappe.client.save',
                args: { doc },
                freeze: true
            });
            removeCachedDoc(doc.name);
            results.success++;
            console.log('Synced doc:', doc.name);
        } catch (e) {
            const excType = e && e.responseJSON && e.responseJSON.exc_type;
            if (isNew && excType === 'DuplicateEntryError') {
                // That hour was captured on the server since this was cached offline;
                // the server copy wins, so stop retrying the stale local one.
                removeCachedDoc(doc.name);
                results.skipped++;
                console.warn('Dropped offline copy, hour already exists on server:', doc.name);
                continue;
            }
            results.failed++;
            console.error('Sync failed for', doc.name, e);
            // Keep in cache if sync fails
        }
    }

    return results;
}

// Listen for online event to trigger sync
window.addEventListener('online', () => {
    if (frappe && frappe.show_alert) {
        frappe.show_alert({
            message: __('Back online! Syncing changes...'),
            indicator: 'orange'
        });
    }
    
    syncCachedDocs().then((results) => {
        if (frappe && frappe.show_alert) {
            if (results.failed > 0) {
                frappe.show_alert({
                    message: __(`Sync complete: ${results.success} succeeded, ${results.failed} failed`),
                    indicator: 'orange'
                });
            } else if (results.success > 0) {
                frappe.show_alert({
                    message: __('All changes synced successfully!'),
                    indicator: 'green'
                });
            }
        }
    });
});

// Listen for offline event to show status
window.addEventListener('offline', () => {
    if (frappe && frappe.show_alert) {
        frappe.show_alert({
            message: __('You are offline. Changes will be saved locally.'),
            indicator: 'orange'
        });
    }
});

// Expose for use in other scripts
window.HourlyProductionOffline = {
    isOnline,
    cacheDoc,
    syncCachedDocs,
    getCachedDocs,
    removeCachedDoc,
    clearAllCachedDocs
};