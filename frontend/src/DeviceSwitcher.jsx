import React, {useEffect, useId, useRef, useState} from 'react'
import {ArrowUpRight, ChevronDown, Pencil, Plus, RefreshCw, Server} from 'lucide-react'
import {api} from './api'
import {Badge, Button, ErrorNotice, Modal} from './ui'

const sourceLabel = {lan: '局域网', tailscale: 'Tailscale', manual: '手动'}
const statusLabel = {true: '主机探测可达', false: '主机探测失败', null: '待检测'}

function DeviceEditor({entry, close, saved}) {
  const [url, setUrl] = useState(entry.url || '')
  const [name, setName] = useState(entry.name || '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState(false)

  async function save(method) {
    setBusy(true)
    setError('')
    try {
      const path = '/devices' + (entry.key ? '/' + encodeURIComponent(entry.key) : '')
      saved(await api(path, {method, body: method === 'DELETE' ? undefined : {url, name}}))
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }

  return <Modal title={entry.key ? '编辑设备地址' : '添加设备'} onClose={() => {if (!busy) close()}}>
    <form className="form" onSubmit={event => {
      event.preventDefault()
      save(removing ? 'DELETE' : entry.key ? 'PUT' : 'POST')
    }}>
      {removing ? <p>移除手动地址 <strong>{entry.url}</strong>？</p> : <>
        <label>控制台 URL<input type="url" autoFocus placeholder="http://192.168.1.10:9786"
          value={url} required maxLength={512} onChange={event => setUrl(event.target.value)}/></label>
        <label>名称（可选）<input value={name} maxLength={100} onChange={event => setName(event.target.value)}/></label>
      </>}
      <ErrorNotice>{error}</ErrorNotice>
      <div className="form-actions">
        {entry.key && !removing && <Button variant="danger" disabled={busy} onClick={() => setRemoving(true)}>删除地址</Button>}
        <Button onClick={removing ? () => setRemoving(false) : close} disabled={busy}>取消</Button>
        <button className={'button ' + (removing ? 'danger' : 'primary')} disabled={busy}>
          {busy ? '处理中…' : removing ? '确认删除' : '保存'}
        </button>
      </div>
    </form>
  </Modal>
}

function DeviceRow({item, edit}) {
  const [primary, ...others] = item.addresses
  const sources = [...new Set(item.addresses.flatMap(address => address.sources))]
  return <li className="device-row">
    <a className="device-main-link" href={primary.url} rel="noreferrer" title={primary.url}>
      <div className="device-name">
        <strong>{item.name}</strong>
        <small>{sources.map(source => sourceLabel[source]).join(' · ')}</small>
      </div>
      <Badge tone={primary.reachable === true ? 'good' : primary.reachable === false ? 'warning' : 'neutral'}>
        {statusLabel[primary.reachable]}
      </Badge>
      <ArrowUpRight size={15} aria-hidden="true"/>
    </a>
    {(others.length > 0 || item.manual.length > 0) && <details className="device-details">
      <summary>{item.addresses.length > 1 ? '访问地址' : '手动地址'}</summary>
      {item.addresses.map(address => <div className="device-address" key={address.url}>
        <a className="text-link" href={address.url} rel="noreferrer">
          <span>{address.url}</span>
          <small>{address.sources.map(source => sourceLabel[source]).join(' · ')} · {statusLabel[address.reachable]}</small>
        </a>
        {item.manual.filter(entry => entry.url === address.url).map(entry => <Button key={entry.key} icon={Pencil}
          aria-label={'编辑地址 ' + entry.url} title="编辑手动地址" onClick={() => edit(entry)}/>)}
      </div>)}
    </details>}
  </li>
}

export default function DeviceSwitcher({hostname}) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [editor, setEditor] = useState(null)
  const panelId = useId()
  const wrapper = useRef(null)
  const toggle = useRef(null)

  async function refresh(signal) {
    setRefreshing(true)
    setError('')
    try {
      const result = await api('/devices/refresh', {method: 'POST', signal})
      if (!signal?.aborted) setData(result)
    } catch (e) {
      if (!signal?.aborted) setError(e.message)
    } finally {
      if (!signal?.aborted) setRefreshing(false)
    }
  }

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setRefreshing(true)
    setError('')
    api('/devices', {signal: controller.signal}).then(result => {
      if (!controller.signal.aborted) setData(result)
    }).catch(() => {}).finally(() => {
      if (!controller.signal.aborted) refresh(controller.signal)
    })
    return () => controller.abort()
  }, [open])

  useEffect(() => {
    if (!open || editor) return
    function outside(event) {
      if (!wrapper.current?.contains(event.target)) setOpen(false)
    }
    function keydown(event) {
      if (event.key === 'Escape') {
        setOpen(false)
        toggle.current?.focus()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keydown)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keydown)
    }
  }, [open, editor])

  return <div className="device-switcher" ref={wrapper}>
    <button ref={toggle} type="button" className="device-toggle" title={hostname}
      aria-label={'切换设备 · ' + hostname} aria-controls={panelId} aria-expanded={open}
      onClick={() => setOpen(value => !value)}>
      <Server size={15} aria-hidden="true"/><span>{hostname}</span><ChevronDown size={14} aria-hidden="true"/>
    </button>
    {open && <section className="device-popover" id={panelId} aria-label="设备切换">
      <div className="device-popover-header">
        <h2>设备</h2>
        <div className="actions">
          <Button icon={RefreshCw} aria-label="刷新设备" title="刷新发现列表" disabled={refreshing}
            onClick={() => refresh()}/>
          <Button icon={Plus} onClick={() => setEditor({})}>手动添加</Button>
        </div>
      </div>
      <div className="device-current">
        <Server size={16} aria-hidden="true"/><strong>{hostname}</strong><Badge tone="good">当前主机</Badge>
      </div>
      {data?.current_manual?.map(entry => <div className="device-address device-current-address" key={entry.key}>
        <span>{entry.url}</span>
        <Button icon={Pencil} aria-label={'编辑地址 ' + entry.url} onClick={() => setEditor(entry)}/>
      </div>)}
      <ErrorNotice>{error}</ErrorNotice>
      {!!data?.devices.length && <ul className="device-list">
        {data.devices.map(item => <DeviceRow key={item.id} item={item} edit={setEditor}/>)}
      </ul>}
      {!data?.devices.length && <p className="device-empty" role="status">
        {refreshing ? '正在发现设备…' : '暂无其他设备，可手动添加控制台 URL。'}
      </p>}
      <p className="device-note">探测来自当前主机。若浏览器连不上，可返回并换一个访问地址。</p>
    </section>}
    {editor && <DeviceEditor entry={editor} close={() => setEditor(null)}
      saved={result => {setData(result); setEditor(null)}}/>}
  </div>
}
