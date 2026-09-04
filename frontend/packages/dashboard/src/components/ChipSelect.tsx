import { useId } from "react";

export default function ChipSelect<T extends string>({
  options,
  selected,
  onChange,
  labelFor,
  label,
}: {
  options: readonly T[];
  selected: readonly T[];
  onChange: (next: T[]) => void;
  labelFor: (value: T) => string;
  label?: string;
}) {
  const labelId = useId();

  function toggle(value: T) {
    onChange(
      selected.includes(value)
        ? selected.filter((v) => v !== value)
        : [...selected, value],
    );
  }

  return (
    <div
      className="field"
      role="group"
      {...(label ? { "aria-labelledby": labelId } : {})}
    >
      {label && (
        <span className="label" id={labelId}>
          {label}
        </span>
      )}
      <div className="chip-list">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className="chip"
            aria-pressed={selected.includes(option)}
            onClick={() => toggle(option)}
          >
            {labelFor(option)}
          </button>
        ))}
      </div>
    </div>
  );
}
