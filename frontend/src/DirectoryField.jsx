import React, {useEffect, useId, useRef, useState} from 'react'
import {ArrowUp, ChevronRight, Folder, FolderOpen, HardDrive, House} from 'lucide-react'
import {api} from './api'
import {Button, ErrorNotice, Loading, Modal} from './ui'

function DirectoryPicker({initial, onClose, onChoose}) {
  const [request, setRequest] = useState({path: initial})
  const [listing, setListing] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showHidden, setShowHidden] = useState(false)
  const list = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    api('/directories?' + new URLSearchParams({path: request.path}), {signal: controller.signal})
      .then(result => {
        if (controller.signal.aborted) return
        setListing(result)
        if (list.current) list.current.scrollTop = 0
      })
      .catch(failure => {
        if (!controller.signal.aborted) setError(failure.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [request])

  function browse(path) {
    setLoading(true)
    setError('')
    setRequest({path})
  }

  const folders = listing?.directories.filter(folder => showHidden || !folder.name.startsWith('.')) || []
  return <Modal title="选择主机目录" className="directory-picker" onClose={onClose}>
    <div className="directory-toolbar">
      <Button icon={ArrowUp} onClick={() => browse(listing.parent)} disabled={loading || !listing?.parent}>上一级</Button>
      <Button icon={House} onClick={() => browse('~')}>主目录</Button>
      <Button icon={HardDrive} onClick={() => browse('/')}>根目录</Button>
    </div>
    <div className="directory-location" aria-live="polite">
      <code>{listing?.path ?? request.path}</code>
    </div>
    <div className="directory-options">
      <label><input type="checkbox" checked={showHidden} onChange={event => setShowHidden(event.target.checked)}/> 显示隐藏目录</label>
    </div>
    <ErrorNotice>{error}</ErrorNotice>
    <div className="directory-list" ref={list} role="region" aria-label="子目录" aria-busy={loading} tabIndex={0}>
      {loading ? <Loading>正在读取目录…</Loading> : folders.length ? <ul>
        {folders.map(folder => <li key={folder.path}>
          <button type="button" className="directory-row" onClick={() => {
            list.current.focus({preventScroll: true})
            browse(folder.path)
          }}>
            <Folder size={17} aria-hidden="true"/><span>{folder.name}</span><ChevronRight size={15} aria-hidden="true"/>
          </button>
        </li>)}
      </ul> : <p className="empty">{listing ? '没有可显示的子目录' : '可从主目录或根目录重新浏览'}</p>}
    </div>
    <div className="form-actions">
      <Button onClick={onClose}>取消</Button>
      <Button variant="primary" onClick={() => onChoose(listing.path)} disabled={loading || !listing}>选用此目录</Button>
    </div>
  </Modal>
}

export default function DirectoryField({label, value, onChange, disabled}) {
  const [open, setOpen] = useState(false)
  const pathId = useId()
  return <div className="directory-field">
    <span>{label}</span>
    <div className="directory-field-row">
      <code id={pathId}>{value === '~' ? '用户主目录（~）' : value}</code>
      <Button icon={FolderOpen} aria-haspopup="dialog" aria-describedby={pathId} disabled={disabled} onClick={() => setOpen(true)}>
        浏览目录
      </Button>
    </div>
    {open && <DirectoryPicker initial={value} onClose={() => setOpen(false)} onChoose={path => {
      onChange(path)
      setOpen(false)
    }}/>}
  </div>
}
