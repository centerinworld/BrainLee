import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Root from './hub/Root.jsx'
import { installApiTokenFetch } from './apiToken.js'

installApiTokenFetch()

// 배포로 화면 조각 파일(해시 이름)이 바뀌면, 이미 열려 있던 탭이 옛 파일을 못 찾아 화면이 깨진다.
// 그런 오류가 나면 최신 화면으로 한 번만 자동 새로고침한다(무한 새로고침 방지: 30초 안에는 다시 하지 않음).
const reloadOnce = () => {
  try {
    const last = Number(sessionStorage.getItem('sd_chunk_reload') || 0)
    if (Date.now() - last < 30000) return
    sessionStorage.setItem('sd_chunk_reload', String(Date.now()))
  } catch { /* 저장소 차단 시에도 1회는 새로고침 */ }
  window.location.reload()
}
window.addEventListener('vite:preloadError', reloadOnce)
window.addEventListener('unhandledrejection', (e) => {
  if (/Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(String(e.reason && (e.reason.message || e.reason)))) reloadOnce()
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
