/* Адрес сервиса (P1-16): общий для service worker'а (importScripts) и попапа.
 *
 * По умолчанию сервис локальный -- 127.0.0.1 / localhost, доступ к ним есть
 * всегда (host_permissions в manifest.json). Любой другой адрес -- «удалённый
 * сервис»: доступ к нему попап просит у Chrome при настройке адреса
 * (optional_host_permissions), и только к этому одному хосту. Токен уходит на
 * удалённый адрес только по https.
 */

const NF_LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost']);

// { local, secure, origin } или null, если адрес не http(s)-URL.
// origin -- шаблон для chrome.permissions: без порта (шаблон с хостом и так
// покрывает любой порт) и без пути.
function nfServiceTarget(base) {
  let u;
  try { u = new URL(String(base || '').trim()); } catch { return null; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
  if (!u.hostname) return null;
  return {
    local: NF_LOCAL_HOSTS.has(u.hostname),
    secure: u.protocol === 'https:',
    origin: `${u.protocol}//${u.hostname}/*`,
  };
}

// Есть ли у расширения доступ к адресу: локальный -- всегда, удалённый --
// только если пользователь его разрешил.
async function nfHasHostAccess(target) {
  if (!target) return false;
  if (target.local) return true;
  try { return await chrome.permissions.contains({ origins: [target.origin] }); } catch { return false; }
}
