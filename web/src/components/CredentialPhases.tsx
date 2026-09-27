const phases = [
  "prepare",
  "plan",
  "plan-operation",
  "apply",
  "destroy",
  "operation",
];

export function CredentialPhases({
  selected,
  onChange,
}: {
  selected?: string[];
  onChange?: (phases: string[]) => void;
}) {
  return (
    <fieldset className="credential-phases">
      <legend>Available during</legend>
      <div className="credential-phase-options">
        {phases.map((phase) => (
          <label key={phase}>
            <input
              type="checkbox"
              name="phase"
              value={phase}
              checked={selected?.includes(phase)}
              onChange={
                onChange
                  ? (event) =>
                      onChange(
                        event.target.checked
                          ? [...(selected ?? []), phase]
                          : (selected ?? []).filter((item) => item !== phase),
                      )
                  : undefined
              }
            />
            {phase}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
