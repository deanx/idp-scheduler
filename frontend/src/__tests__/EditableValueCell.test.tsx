/**
 * Tests for EditableValueCell type-aware validation (T-02.3.2, DoD automated tests).
 *
 * The pure validateFieldValue() function is tested directly for type-aware
 * behaviour. Component render tests confirm the input renders correctly and
 * shows error messages after blur.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  validateFieldValue,
  EditableValueCell,
} from "../components/review/EditableValueCell";

// ---- pure validation tests

describe("validateFieldValue()", () => {
  describe("number type", () => {
    it("accepts an integer", () => {
      expect(validateFieldValue("42", "number")).toBeNull();
    });

    it("accepts a decimal", () => {
      expect(validateFieldValue("1250.99", "number")).toBeNull();
    });

    it("accepts a negative number", () => {
      expect(validateFieldValue("-7.5", "number")).toBeNull();
    });

    it("rejects an empty string", () => {
      expect(validateFieldValue("", "number")).not.toBeNull();
    });

    it("rejects a non-numeric string", () => {
      expect(validateFieldValue("abc", "number")).not.toBeNull();
    });

    it("rejects a number with commas (1,250)", () => {
      // The schema pattern is /^-?[0-9]+(\.[0-9]+)?$/ — comma-formatted numbers fail
      expect(validateFieldValue("1,250.00", "number")).not.toBeNull();
    });

    it("rejects a trailing dot without decimals (1.)", () => {
      expect(validateFieldValue("1.", "number")).not.toBeNull();
    });
  });

  describe("date type", () => {
    it("accepts a non-empty date string", () => {
      expect(validateFieldValue("2024-06-28", "date")).toBeNull();
    });

    it("accepts any non-empty string (format validated server-side)", () => {
      expect(validateFieldValue("28/06/2024", "date")).toBeNull();
    });

    it("rejects an empty string", () => {
      expect(validateFieldValue("", "date")).not.toBeNull();
    });
  });

  describe("id type", () => {
    it("accepts a non-empty id", () => {
      expect(validateFieldValue("INV-001", "id")).toBeNull();
    });

    it("rejects an empty id", () => {
      expect(validateFieldValue("", "id")).not.toBeNull();
    });
  });

  describe("text type", () => {
    it("accepts any string including empty (text may be blank)", () => {
      expect(validateFieldValue("", "text")).toBeNull();
      expect(validateFieldValue("any value", "text")).toBeNull();
    });
  });
});

// ---- component render + behaviour tests

describe("EditableValueCell component", () => {
  it("renders an input with the field label", () => {
    render(
      <EditableValueCell
        fieldName="invoice_total"
        value="1250.00"
        fieldType="number"
        onChange={() => undefined}
      />,
    );
    expect(screen.getByLabelText("value for invoice_total")).toBeDefined();
  });

  it("does not show error before the user touches the field (no eager validation)", () => {
    render(
      <EditableValueCell
        fieldName="amount"
        value="not-a-number"
        fieldType="number"
        onChange={() => undefined}
      />,
    );
    // Error should not appear before blur
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a validation error after blur on an invalid number", () => {
    render(
      <EditableValueCell
        fieldName="amount"
        value="abc"
        fieldType="number"
        onChange={() => undefined}
      />,
    );
    const input = screen.getByLabelText("value for amount");
    fireEvent.blur(input);
    expect(screen.getByRole("alert")).toBeDefined();
    expect(screen.getByRole("alert").textContent).toContain("number");
  });

  it("shows no error after blur on a valid number", () => {
    render(
      <EditableValueCell
        fieldName="amount"
        value="42.50"
        fieldType="number"
        onChange={() => undefined}
      />,
    );
    const input = screen.getByLabelText("value for amount");
    fireEvent.blur(input);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("calls onCommit with valid=false for an invalid value on blur", () => {
    const onCommit = vi.fn();
    render(
      <EditableValueCell
        fieldName="total"
        value="not-a-number"
        fieldType="number"
        onChange={() => undefined}
        onCommit={onCommit}
      />,
    );
    fireEvent.blur(screen.getByLabelText("value for total"));
    expect(onCommit).toHaveBeenCalledWith("not-a-number", false);
  });

  it("calls onCommit with valid=true for a valid value on blur", () => {
    const onCommit = vi.fn();
    render(
      <EditableValueCell
        fieldName="total"
        value="99.99"
        fieldType="number"
        onChange={() => undefined}
        onCommit={onCommit}
      />,
    );
    fireEvent.blur(screen.getByLabelText("value for total"));
    expect(onCommit).toHaveBeenCalledWith("99.99", true);
  });

  it("is disabled when disabled prop is set", () => {
    render(
      <EditableValueCell
        fieldName="total"
        value="1.00"
        fieldType="number"
        onChange={() => undefined}
        disabled
      />,
    );
    const input = screen.getByLabelText("value for total") as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });
});
