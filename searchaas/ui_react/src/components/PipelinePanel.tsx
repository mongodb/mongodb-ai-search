import { Button, CodeBlock } from "./UI";

export default function PipelinePanel({
  pipeline, effectiveStrategy, actual = false,
}: {
  pipeline: unknown;
  effectiveStrategy: string;
  /** True when this is the real pipeline captured from the executed query
   *  (vs. a client-side reconstruction fallback). */
  actual?: boolean;
}) {
  const text = JSON.stringify(pipeline, null, 2);

  const download = () => {
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${effectiveStrategy}-pipeline.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: 8 }}>
        <p className="muted" style={{ margin: 0 }}>
          {actual
            ? <>Actual aggregation executed for <b>{effectiveStrategy}</b> — copy into Compass or <code>mongosh</code>.</>
            : <>Reconstructed (approximate) aggregation for <b>{effectiveStrategy}</b> — copy into Compass or <code>mongosh</code>.</>}
        </p>
        <Button size="sm" onClick={download}>⬇ Download JSON</Button>
      </div>
      <CodeBlock code={text} />
    </div>
  );
}
