import React, {useEffect, useState} from 'react'
import {ExternalLink, RefreshCw} from 'lucide-react'
import {api} from './api'
import {Button, ErrorNotice, Loading, PageTitle} from './ui'

export default function DshPage({installed}) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (installed !== true) return
    const controller = new AbortController()
    api('/dsh/web', {method: 'POST', signal: controller.signal})
      .then(result => setUrl(result.url))
      .catch(error => {if (!controller.signal.aborted) setError(error.message)})
    return () => controller.abort()
  }, [attempt, installed])

  return <div className="page dsh-page">
    <PageTitle title="DeepSeek Harness">
      {url && <a className="button" href={url} target="_blank" rel="noreferrer"><ExternalLink size={16}/> 新标签页打开</a>}
    </PageTitle>
    {installed === false ? <section className="card"><div className="empty">
        <strong>主机未安装 DeepSeek Harness</strong>
        <p>在服务器运行 <code>./console bootstrap --with-ai</code> 安装，然后刷新此页。</p>
      </div></section>
      : error ? <div className="dsh-start-error"><ErrorNotice>{error}</ErrorNotice><Button icon={RefreshCw} onClick={() => {setError(''); setAttempt(value => value + 1)}}>重试</Button></div>
      : url ? <iframe className="card dsh-frame" src={url} title="DeepSeek Harness 官方 Web"/>
        : <Loading>正在启动 DeepSeek Harness…</Loading>}
  </div>
}
