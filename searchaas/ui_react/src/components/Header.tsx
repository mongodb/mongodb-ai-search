export default function Header({ backend }: { backend: string }) {
  return (
    <div className="header">
      <div>
        <h1>SearchaaS — Retrieval Tester</h1>
        <p>
          MongoDB Atlas · Phase 1 · Vector / Full-text / Hybrid / Graph / Parent-doc
        </p>
      </div>
      <div className="header-right">
        Backend<br /><b>{backend}</b>
      </div>
    </div>
  );
}
