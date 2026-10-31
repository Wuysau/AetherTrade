"""Apply one exact-source LGPL extension; never patch an installed SDK."""
from pathlib import Path
from hashlib import sha256
import difflib
import sys

CLIENT_SHA = 'ee606aeb333f639398dd97c060566b29e0f5b029b32024760da4a9d55420eb41'
METHOD = r'''
    /// AetherTrade qualification-only post-quota dispatch on the owner OS thread.
    /// The guard is a trusted internal context manager, not a business API.
    #[pyo3(name = "request_guarded")]
    #[pyo3(signature = (method, url, guard, headers=None, body=None, keys=None, timeout_secs=2, budget_ms=2000))]
    fn py_request_guarded(
        &self,
        method: HttpMethod,
        url: String,
        guard: Py<PyAny>,
        headers: Option<HashMap<String, String>>,
        body: Option<Vec<u8>>,
        keys: Option<Vec<String>>,
        timeout_secs: u64,
        budget_ms: u64,
        py: Python<'_>,
    ) -> PyResult<HttpResponse> {
        let parsed = reqwest::Url::parse(&url).map_err(to_pyvalue_err)?;
        if parsed.scheme() != "http" || parsed.host_str() != Some("127.0.0.1")
            || !parsed.username().is_empty() || parsed.password().is_some()
            || parsed.fragment().is_some() || reqwest::Method::from(method) != reqwest::Method::POST
            || timeout_secs == 0 || timeout_secs > 2 || budget_ms == 0 || budget_ms > 5000 {
            return Err(to_pyvalue_err("GUARDED_LOOPBACK_POST_ONLY"));
        }
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(to_pyvalue_err("GUARDED_NESTED_TOKIO_RUNTIME_REJECTED"));
        }
        let deadline = tokio::time::Instant::now() + Duration::from_millis(budget_ms);
        let mut client = self.clone();
        // Preserve every shared rate limiter. One HTTP/1 attempt, no proxy,
        // redirect, protocol fallback or automatic retry of an uncertain write.
        client.client.client = reqwest::Client::builder()
            .http1_only().no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(Duration::from_secs(timeout_secs))
            .build().map_err(to_pyruntime_err)?;
        py.detach(move || {
            // No spawn: ExecutionOwner is tied to the calling OS thread.
            let runtime = blocking_http_runtime()?;
            runtime.block_on(async move {
                let keys = keys.map(into_ustr_vec);
                tokio::time::timeout_at(deadline, client.await_rate_limits(keys.as_deref()))
                    .await.map_err(|_| to_pyruntime_err("GUARDED_ORIGINAL_BUDGET_EXPIRED"))?;
                Python::attach(|py| {
                    py.check_signals()?;
                    guard.call_method0(py, "__enter__")
                })?;
                let result = if tokio::time::Instant::now() >= deadline {
                    Err(to_pyruntime_err("GUARDED_ORIGINAL_BUDGET_EXPIRED"))
                } else {
                    match tokio::time::timeout_at(deadline, client.client.send_request(
                        method.into(), url, None, headers, body, Some(timeout_secs),
                    )).await {
                        Ok(result) => result.map_err(HttpClientError::into_py_err),
                        Err(_) => Err(to_pyruntime_err("GUARDED_ORIGINAL_BUDGET_EXPIRED")),
                    }
                };
                // Always release the owner context, including transport failure.
                // Context suppression cannot turn an uncertain write into success.
                Python::attach(|py| {
                    guard.call_method1(py, "__exit__", (py.None(), py.None(), py.None()))
                })?;
                result
            })
        })
    }
'''

def apply(root):
    root = Path(root)
    client = root / 'crates/network/src/http/client.rs'
    if sha256(client.read_bytes()).hexdigest() != CLIENT_SHA:
        raise ValueError('PINNED_CLIENT_SOURCE_CHANGED')
    target = root / 'crates/network/src/python/http.rs'
    if sha256(target.read_bytes()).hexdigest() != 'c6b767c6ee5ef8860a40913fa2bae8ab7892d58a67ad667617a56e1ea43438b6':
        raise ValueError('PINNED_PYTHON_HTTP_SOURCE_CHANGED')
    before = target.read_text(encoding='utf-8')
    if 'fn py_request_guarded' in before:
        raise ValueError('SOURCE_ALREADY_PATCHED')
    needle = 'impl HttpClient {\n'
    assert before.count(needle) == 1
    after = before.replace(needle, needle + METHOD, 1)
    patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile='a/crates/network/src/python/http.rs', tofile='b/crates/network/src/python/http.rs', n=0))
    build = root / 'build.py'
    if sha256(build.read_bytes()).hexdigest() != '8a8e46de9c58b83bbfdb3b7415538d2444f9dd126d61c66a8a63416606e9bc40':
        raise ValueError('PINNED_BUILD_SOURCE_CHANGED')
    build_before = build.read_text(encoding='utf-8')
    assert build_before.count('nthreads = os.cpu_count() or 1') == 1
    assert build_before.count('cmd.parallel = os.cpu_count()') == 1
    build_after = build_before.replace('nthreads = os.cpu_count() or 1', 'nthreads = min(2, os.cpu_count() or 1)')
    build_after = build_after.replace('cmd.parallel = os.cpu_count()', 'cmd.parallel = min(2, os.cpu_count() or 1)')
    patch += ''.join(difflib.unified_diff(build_before.splitlines(True), build_after.splitlines(True),
        fromfile='a/build.py', tofile='b/build.py', n=0))
    target.write_text(after, encoding='utf-8', newline='\n')
    build.write_text(build_after, encoding='utf-8', newline='\n')
    return patch

if __name__ == '__main__':
    print(apply(sys.argv[1]), end='')
