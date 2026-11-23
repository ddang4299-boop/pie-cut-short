/**
 * PieCut logo: a cookie as a film director — tilted beret, dark sunglasses with a violet
 * glint, chocolate chips and a confident smirk. Kept in sync with public/piecut.svg.
 */
export function Logo({ className = "size-10", title }: { className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      <defs>
        <radialGradient id="pc-dough" cx="40%" cy="35%" r="70%">
          <stop offset="0" stopColor="#F2C27E" />
          <stop offset="0.65" stopColor="#D9974F" />
          <stop offset="1" stopColor="#A8662E" />
        </radialGradient>
        <linearGradient id="pc-lens" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2A2A33" />
          <stop offset="1" stopColor="#050507" />
        </linearGradient>
      </defs>
      {/* cookie with a slightly bumpy edge */}
      <path
        d="M32 12.5c3.2 0 4.6 1.6 7.4 2.3 2.9.8 4.9-.2 7.2 1.8 2.2 1.9 1.9 4.1 3.4 6.6 1.5 2.6 3.7 3.6 4 6.6.4 3-1.3 4.6-1.6 7.5-.3 2.9 1 5-.6 7.6-1.7 2.6-4 2.7-6.3 4.6-2.3 1.9-2.9 4.1-5.8 5.1-2.9.9-4.8-.5-7.7-.5s-4.8 1.4-7.7.5c-2.9-1-3.5-3.2-5.8-5.1-2.3-1.9-4.6-2-6.3-4.6-1.6-2.6-.3-4.7-.6-7.6-.3-2.9-2-4.5-1.6-7.5.3-3 2.5-4 4-6.6 1.5-2.5 1.2-4.7 3.4-6.6 2.3-2 4.3-1 7.2-1.8 2.8-.7 4.2-2.3 7.4-2.3z"
        fill="url(#pc-dough)"
        stroke="#8A4F21"
        strokeWidth="1.2"
      />
      {/* chocolate chips */}
      <g fill="#4A2A15">
        <path d="M18.5 43.5l2.6-1.2 1.4 2.4-2.3 1.6z" />
        <path d="M41 46.5l2.8-.6.8 2.6-2.7.9z" />
        <path d="M46.5 27.5l2.4.4-.3 2.6-2.5-.5z" />
        <path d="M16.5 30l2.2-.9 1.1 2.2-2.2 1z" />
        <circle cx="30" cy="50.5" r="1.3" />
      </g>
      {/* director's beret, flat and slouched to the left, with a stalk */}
      <g transform="rotate(-13 32 18)">
        <path d="M9.5 21.5c-1.2-6.8 8.8-12.6 21.5-12.8 12.4-.2 20.6 4.6 20.3 10.2-.1 1.6-1.3 2.6-3 2.9-11.6-2.6-24.3-2.4-35.9.9-1.5.2-2.6-.1-2.9-1.2z" fill="#111114" stroke="#3A3A45" strokeWidth="0.8" />
        <path d="M13.5 21.6c10.8-2.8 23.4-3 34.6-.6" stroke="#2E2E37" strokeWidth="1.5" strokeLinecap="round" fill="none" />
        <path d="M31.5 8.8c-.1-1.8.7-3 2.1-3.4" stroke="#111114" strokeWidth="2.2" strokeLinecap="round" fill="none" />
      </g>
      {/* sunglasses */}
      <path d="M12.5 27.5h39" stroke="#0B0B0F" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M14 27.5h14.5c.4 4.6-2.2 8.3-7 8.3-4.6 0-7.5-3.4-7.5-8.3z" fill="url(#pc-lens)" stroke="#0B0B0F" strokeWidth="1.2" />
      <path d="M35.5 27.5H50c0 4.9-2.9 8.3-7.5 8.3-4.8 0-7.4-3.7-7-8.3z" fill="url(#pc-lens)" stroke="#0B0B0F" strokeWidth="1.2" />
      <path d="M17.5 29.5l3.5 3.6M39 29.5l3.5 3.6" stroke="#7C5CFF" strokeWidth="1.4" strokeLinecap="round" opacity="0.9" />
      <path d="M22.5 29.3l1.4 1.4M44 29.3l1.4 1.4" stroke="#FFFFFF" strokeWidth="1.1" strokeLinecap="round" opacity="0.7" />
      {/* smirk */}
      <path d="M25 42.2c3.6 2.3 9 2.4 13.4-.6" stroke="#5A3217" strokeWidth="2" strokeLinecap="round" fill="none" />
      <path d="M38.2 41.7l1.6-1.2" stroke="#5A3217" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
