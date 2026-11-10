import { useEffect, useState } from 'react'

// Carrossel da tela de login (referencia: a versao no ar esta no bundle dist/assets).
const SLIDES = [
  { titulo: 'Escritório Modelo Advogados', sub: 'Advocacia trabalhista com estratégia, técnica e cuidado com cada caso.', silver: true },
  { titulo: 'Direito do Trabalho, dos dois lados da mesa', sub: 'Assessoria a empresas e defesa de quem trabalha, com a mesma seriedade.' },
  { titulo: 'Tecnologia a serviço do cliente', sub: 'Atendimento, prazos e peças acompanhados de perto, todos os dias.' },
]

export default function CarouselHero() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % SLIDES.length), 5500)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="carousel">
      <div className="hero-overlay" />
      {SLIDES.map((s, n) => (
        <div key={n} className={`c-slide ${n === i ? 'active' : ''}`}>
          <div className="c-caption">
            <h1 className={s.silver ? 'silver' : ''}>{s.titulo}</h1>
            <p>{s.sub}</p>
          </div>
        </div>
      ))}
      <div className="c-dots">
        {SLIDES.map((_, n) => (
          <button key={n} className={n === i ? 'on' : ''} onClick={() => setI(n)} aria-label={`Slide ${n + 1}`} />
        ))}
      </div>
    </div>
  )
}
