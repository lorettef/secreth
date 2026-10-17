import { type FormEvent, useState } from "react";
import { ApiError } from "../lib/api";
import type { ProfileInput } from "../models";

interface ProfileFormProps {
  title: string;
  subtitle: string;
  submitLabel: string;
  initial?: Partial<ProfileInput>;
  onSubmit: (values: ProfileInput) => Promise<void>;
}

type FieldErrors = Partial<Record<keyof ProfileInput, string>>;

const serverField: Partial<Record<string, keyof ProfileInput>> = {
  FIRST_NAME_REQUIRED: "first_name",
  LAST_NAME_REQUIRED: "last_name",
  SCHOOL_NICKNAME_REQUIRED: "school_nickname",
  PROFILE_FIELD_TOO_LONG: "school_nickname",
  SCHOOL_NICKNAME_CONFLICT: "school_nickname",
};

const serverMessage: Record<string, string> = {
  INVITE_INVALID: "Приглашение недействительно или уже использовано.",
  ONBOARDING_ACCESS_DENIED: "Доступ к клубу ограничен.",
  NETWORK_ERROR: "Не удалось связаться с сервером. Проверьте соединение и повторите.",
  SCHOOL_NICKNAME_CONFLICT: "Этот ник уже занят в клубе.",
};

export function ProfileForm({
  title,
  subtitle,
  submitLabel,
  initial,
  onSubmit,
}: ProfileFormProps) {
  const [values, setValues] = useState<ProfileInput>({
    first_name: initial?.first_name ?? "",
    last_name: initial?.last_name ?? "",
    school_nickname: initial?.school_nickname ?? "",
  });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function validate(): FieldErrors {
    const errors: FieldErrors = {};
    if (!values.first_name.trim()) errors.first_name = "Введите имя.";
    if (!values.last_name.trim()) errors.last_name = "Введите фамилию.";
    if (!values.school_nickname.trim()) {
      errors.school_nickname = "Введите ник Школы 21.";
    }
    return errors;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const errors = validate();
    setFieldErrors(errors);
    setFormError(null);
    if (Object.keys(errors).length) return;
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (error) {
      const code = error instanceof ApiError ? error.code : "NETWORK_ERROR";
      const field = serverField[code];
      if (field) {
        setFieldErrors({ [field]: serverMessage[code] ?? "Проверьте значение." });
      } else {
        setFormError(serverMessage[code] ?? "Не удалось завершить регистрацию.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  function update(field: keyof ProfileInput, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
  }

  return (
    <main className="auth-shell">
      <div className="auth-card">
        <header className="auth-card__header">
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </header>
        {formError && <div className="alert alert--error">{formError}</div>}
        <form onSubmit={submit} noValidate>
          <Field
            label="Имя"
            name="first_name"
            value={values.first_name}
            error={fieldErrors.first_name}
            autoComplete="given-name"
            onChange={(value) => update("first_name", value)}
          />
          <Field
            label="Фамилия"
            name="last_name"
            value={values.last_name}
            error={fieldErrors.last_name}
            autoComplete="family-name"
            onChange={(value) => update("last_name", value)}
          />
          <Field
            label="Ник Школы 21"
            name="school_nickname"
            value={values.school_nickname}
            error={fieldErrors.school_nickname}
            autoComplete="username"
            onChange={(value) => update("school_nickname", value)}
          />
          <button className="primary-button" type="submit" disabled={submitting}>
            {submitting ? "Отправляем…" : submitLabel}
          </button>
        </form>
      </div>
    </main>
  );
}

interface FieldProps {
  label: string;
  name: string;
  value: string;
  error?: string;
  autoComplete: string;
  onChange: (value: string) => void;
}

function Field({ label, name, value, error, autoComplete, onChange }: FieldProps) {
  const errorId = `${name}-error`;
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <input
        name={name}
        value={value}
        autoComplete={autoComplete}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error && (
        <span className="field__error" id={errorId}>
          {error}
        </span>
      )}
    </label>
  );
}
