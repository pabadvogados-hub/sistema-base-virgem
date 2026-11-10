export default function EmBreve({ titulo }) {
  return (
    <div className="panel" style={{ textAlign: 'center', padding: 60 }}>
      <div style={{ fontSize: 44, marginBottom: 12 }}>🚧</div>
      <h2>{titulo}</h2>
      <p className="p-sub" style={{ marginTop: 8 }}>
        Este módulo já tem a integração pronta no back-end e será ligado na próxima etapa.
      </p>
    </div>
  )
}
