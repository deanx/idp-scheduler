/**
 * EditableValueCell — T-02.3.2
 *
 * A type-aware inline input for a single golden field value.
 * Validation rules mirror the committed golden schema (golden_schema_v1.json):
 *   - number : must match /^-?[0-9]+(\.[0-9]+)?$/
 *   - date   : must be a non-empty string (format validated server-side)
 *   - id     : any non-empty string
 *   - text   : any string (empty allowed — the field can be blank)
 *
 * The server is the authoritative validator; this client-side check is the
 * courtesy that catches obvious type mismatches before sending a PATCH.
 */

import { useState } from "react";
import type { FieldType } from "../../api";

const NUMBER_RE = /^-?[0-9]+(\.[0-9]+)?$/;

/**
 * Pure validation function — exported so tests can exercise it directly
 * without mounting a component.
 */
export function validateFieldValue(value: string, fieldType: FieldType): string | null {
  switch (fieldType) {
    case "number":
      if (value === "") return "number value cannot be empty";
      if (!NUMBER_RE.test(value)) return "must be a number (digits, optional decimal)";
      return null;
    case "date":
      if (value === "") return "date value cannot be empty";
      return null; // format validated server-side
    case "id":
      if (value === "") return "id value cannot be empty";
      return null;
    case "text":
      return null; // text may be empty
  }
}

export interface EditableValueCellProps {
  fieldName: string;
  value: string;
  fieldType: FieldType;
  /** Called on every change — value may be invalid mid-type. */
  onChange: (value: string) => void;
  /** Called when the user commits (blur or Enter). */
  onCommit?: (value: string, valid: boolean) => void;
  /** Disable the input (e.g. while a PATCH request is in flight). */
  disabled?: boolean;
}

export function EditableValueCell({
  fieldName,
  value,
  fieldType,
  onChange,
  onCommit,
  disabled = false,
}: EditableValueCellProps) {
  const [touched, setTouched] = useState(false);
  const error = touched ? validateFieldValue(value, fieldType) : null;

  const inputType =
    fieldType === "number" ? "text" : // free text so pattern can show custom error
    fieldType === "date"   ? "text" :
    "text";

  const handleBlur = () => {
    setTouched(true);
    onCommit?.(value, validateFieldValue(value, fieldType) === null);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      setTouched(true);
      onCommit?.(value, validateFieldValue(value, fieldType) === null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <input
        id={`field-value-${fieldName}`}
        type={inputType}
        value={value}
        disabled={disabled}
        aria-label={`value for ${fieldName}`}
        aria-invalid={error !== null}
        aria-describedby={error ? `field-error-${fieldName}` : undefined}
        style={{
          width: "100%",
          fontFamily: "var(--mono)",
          fontSize: 12,
          borderColor: error ? "var(--fail)" : undefined,
        }}
        onChange={(e) => onChange(e.target.value)}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
      />
      {error && (
        <span
          id={`field-error-${fieldName}`}
          className="small"
          style={{ color: "var(--fail)" }}
          role="alert"
        >
          {error}
        </span>
      )}
    </div>
  );
}
