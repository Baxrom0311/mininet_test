/* Bridge — window.pywebview.api ustidan yupqa async o'rov.
 *
 * Har bir chaqiruv Promise qaytaradi. pywebview tayyor bo'lguncha kutadi
 * (pywebviewready hodisasi). Agar pywebview umuman bo'lmasa (masalan, HTML
 * brauzerda ochiq preview holatida), MOCK javob qaytariladi — shu tufayli
 * ekranlarni backend'siz ham chizib ko'rish mumkin.
 *
 * BACKEND KONTRAKTI (studio/api.py da aynan shu nomlar bilan bo'lishi shart):
 *   get_config()                       -> {ssh_host, ssh_user, ssh_pass, wsl_distro, default_duration, default_seed, results_dir, theme}
 *   save_config(cfg)                   -> {ok}
 *   test_ssh(cfg)                      -> {ok, message}
 *   list_topologies()                  -> {builtin:[..], custom:[..]}
 *   get_topology(name)                 -> {switches, hosts, links(obj "s1-s2"->params), access_links, positions}
 *   save_topology(name, topo)          -> {ok, error, path}
 *   delete_topology(name)              -> {ok, error}
 *   validate_topology(topo)            -> {ok, error, pairs, missing}
 *   routing_modes()                    -> [..11..]
 *   compute_paths_summary(name, mode)  -> {pairs, sample:[{src,dst,path}], avg_hops}
 *   dataset_folders()                  -> [{path, label, has_datasets}]
 *   list_datasets(folder)             -> {folder, tables:[{name, rows}], routing_modes:[..], errors:{}}
 *   query(folder, sql)                 -> {columns:[..], rows:[[..]], error}
 *   routing_compare(folder)           -> {modes:[..], avg_rtt:[..], box:{mode:[vals]}}
 *   anomaly_summary(folder)            -> {types:[{type,count}], hourly:[24 ints]}
 *   dns_summary(folder)                -> {stages:[{stage, p50, p90}], cache:{hit, miss}}
 *   qos_summary(folder)                -> {bands:[{band,count}], impairments:[{event,count}]}
 *   dashboard_summary(folder)          -> {tiles:[{label,value,hint,accent}], recent_runs:[..]}
 *   run_simulation(params)             -> {ok, run_id, error}   params:{topology,routing,duration,seed}
 *   run_status(run_id)                 -> {state, progress, phase, log_tail:[..], done, error}
 *   stop_simulation(run_id)            -> {ok}
 *   list_runs()                        -> [{run_id, topology, routing, state, ts}]
 *   import_results(run_id)             -> {ok, folder, error}
 */
(function () {
  let readyPromise = null;

  function whenReady() {
    if (readyPromise) return readyPromise;
    readyPromise = new Promise((resolve) => {
      if (window.pywebview && window.pywebview.api) return resolve(true);
      let settled = false;
      const done = (v) => { if (!settled) { settled = true; resolve(v); } };
      window.addEventListener('pywebviewready', () => done(true), { once: true });
      // Timeout -> mock rejimi (brauzer preview)
      setTimeout(() => done(false), 1500);
    });
    return readyPromise;
  }

  const MOCK = {
    get_config: () => ({ ssh_host: '', ssh_user: 'incubation', ssh_pass: '', wsl_distro: 'Ubuntu-24.04', default_duration: 300, default_seed: 42, results_dir: 'results', theme: 'dark' }),
    routing_modes: () => ['l2_learn', 'rip', 'ospf', 'isis', 'eigrp', 'bgp', 'ecmp', 'spf', 'policy', 'static', 'hybrid'],
    list_topologies: () => ({ builtin: ['three_as', 'five_as', 'datacenter', 'campus'], custom: [] }),
    dataset_folders: () => [{ path: 'results/combined', label: 'combined', has_datasets: true }],
    list_datasets: () => ({ folder: 'results/combined', tables: [], routing_modes: [], errors: {} }),
    dashboard_summary: () => ({ tiles: [], recent_runs: [] }),
    list_runs: () => [],
  };

  const handler = {
    get(_t, name) {
      if (name === 'then') return undefined; // Promise-emas
      return async (...args) => {
        const ok = await whenReady();
        if (ok && window.pywebview && window.pywebview.api && window.pywebview.api[name]) {
          return window.pywebview.api[name](...args);
        }
        // mock fallback
        if (MOCK[name]) return MOCK[name](...args);
        console.warn('[Bridge] backend method yo\'q:', name);
        return null;
      };
    },
  };

  window.Bridge = new Proxy({}, handler);
})();
