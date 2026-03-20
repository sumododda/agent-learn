'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import {
  getProviderRegistry,
  getProviders,
  saveProvider,
  updateProvider,
  deleteProvider,
  testProvider,
  setDefaultProvider,
} from '@/lib/api';
import type { ProviderDefinition, ProviderConfig } from '@/lib/types';

function PasswordModal({
  open,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  onConfirm: (pw: string) => void;
  onCancel: () => void;
}) {
  const [pw, setPw] = useState('');

  useEffect(() => {
    if (open) setPw('');
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 w-full max-w-sm space-y-4">
        <h3 className="text-lg font-semibold text-white">Confirm Password</h3>
        <p className="text-sm text-gray-400">
          Enter your account password to encrypt your API keys.
        </p>
        <input
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="Account password"
          className="w-full px-4 py-3 bg-gray-800 border border-gray-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter' && pw) onConfirm(pw);
          }}
        />
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => pw && onConfirm(pw)}
            disabled={!pw}
            className="px-4 py-2 text-sm bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition-colors"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const router = useRouter();
  const { getToken, isSignedIn, isLoaded } = useAuth();

  const [registry, setRegistry] = useState<Record<string, ProviderDefinition>>({});
  const [configs, setConfigs] = useState<ProviderConfig[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [extraFields, setExtraFields] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [passwordModalOpen, setPasswordModalOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<'save' | 'update' | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [loadingData, setLoadingData] = useState(true);

  const fetchData = useCallback(async () => {
    const token = await getToken();
    const [reg, cfgs] = await Promise.all([
      getProviderRegistry(token),
      getProviders(token),
    ]);
    setRegistry(reg);
    setConfigs(cfgs);
    setLoadingData(false);
    return { reg, cfgs };
  }, [getToken]);

  useEffect(() => {
    if (isLoaded && !isSignedIn) {
      router.push('/login');
      return;
    }
    if (isLoaded && isSignedIn) {
      fetchData().then(({ reg, cfgs }) => {
        const keys = Object.keys(reg);
        if (keys.length > 0 && !selectedProvider) {
          // Prefer the first configured provider, else first in registry
          const firstConfigured = cfgs.find((c) => keys.includes(c.provider));
          setSelectedProvider(firstConfigured?.provider || keys[0]);
        }
      });
    }
  }, [isLoaded, isSignedIn, router, fetchData, selectedProvider]);

  // When selectedProvider changes, reset form
  useEffect(() => {
    if (!selectedProvider) return;
    const def = registry[selectedProvider];
    const cfg = configs.find((c) => c.provider === selectedProvider);

    if (def) {
      const vals: Record<string, string> = {};
      for (const field of def.fields) {
        // For secret fields on existing configs, leave empty (placeholder will show hint)
        vals[field.key] = '';
      }
      setFormValues(vals);
      setExtraFields(cfg?.extra_fields || {});
    }
    setTestResult(null);
    setError(null);
    setSuccessMsg(null);
    setConfirmDelete(false);
  }, [selectedProvider, registry, configs]);

  const providerKeys = Object.keys(registry);
  const currentDef = selectedProvider ? registry[selectedProvider] : null;
  const currentConfig = selectedProvider
    ? configs.find((c) => c.provider === selectedProvider)
    : null;
  const isConfigured = !!currentConfig;

  function handleFieldChange(key: string, value: string) {
    setFormValues((prev) => ({ ...prev, [key]: value }));
  }

  function handleExtraFieldChange(key: string, value: string) {
    setExtraFields((prev) => ({ ...prev, [key]: value }));
  }

  async function handleTest() {
    if (!selectedProvider || !currentDef) return;
    setTesting(true);
    setTestResult(null);
    setError(null);

    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      for (const field of currentDef.fields) {
        if (formValues[field.key]) {
          credentials[field.key] = formValues[field.key];
        }
      }
      await testProvider(selectedProvider, { credentials, extra_fields: extraFields }, token);
      setTestResult({ ok: true, message: 'Connection successful' });
    } catch (err) {
      setTestResult({
        ok: false,
        message: err instanceof Error ? err.message : 'Test failed',
      });
    } finally {
      setTesting(false);
    }
  }

  function handleSaveClick() {
    setError(null);
    setSuccessMsg(null);
    setPendingAction(isConfigured ? 'update' : 'save');
    setPasswordModalOpen(true);
  }

  async function handlePasswordConfirm(password: string) {
    setPasswordModalOpen(false);
    if (!selectedProvider || !currentDef) return;
    setSaving(true);
    setError(null);

    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      for (const field of currentDef.fields) {
        if (formValues[field.key]) {
          credentials[field.key] = formValues[field.key];
        }
      }

      if (pendingAction === 'update') {
        await updateProvider(
          selectedProvider,
          { credentials, extra_fields: extraFields, password },
          token
        );
      } else {
        await saveProvider(
          { provider: selectedProvider, credentials, extra_fields: extraFields, password },
          token
        );
      }

      setSuccessMsg('Provider saved successfully');
      await fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
      setPendingAction(null);
    }
  }

  async function handleDelete() {
    if (!selectedProvider) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const token = await getToken();
      await deleteProvider(selectedProvider, token);
      setSuccessMsg('Provider removed');
      setConfirmDelete(false);
      await fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleSetDefault() {
    if (!selectedProvider) return;
    setSaving(true);
    setError(null);
    try {
      const token = await getToken();
      await setDefaultProvider(selectedProvider, token);
      setSuccessMsg('Default provider updated');
      await fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set default');
    } finally {
      setSaving(false);
    }
  }

  if (!isLoaded || !isSignedIn) {
    return (
      <div className="text-center text-gray-400 mt-20">Loading...</div>
    );
  }

  if (loadingData) {
    return (
      <div className="text-center text-gray-400 mt-20">Loading providers...</div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      <div className="flex gap-6">
        {/* Left column: provider list */}
        <div className="w-64 shrink-0 space-y-2">
          <div className="text-gray-500 text-xs uppercase tracking-wider mb-3">
            AI Providers
          </div>
          {providerKeys.map((key) => {
            const def = registry[key];
            const cfg = configs.find((c) => c.provider === key);
            const isActive = selectedProvider === key;

            return (
              <button
                key={key}
                onClick={() => setSelectedProvider(key)}
                className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                  isActive
                    ? 'bg-gray-800 border-purple-500'
                    : 'bg-gray-900 border-gray-700 hover:border-gray-500'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-white">
                    {def.name}
                  </span>
                  {cfg?.is_default && (
                    <span className="text-[10px] uppercase tracking-wider bg-purple-600/20 text-purple-300 px-1.5 py-0.5 rounded">
                      Default
                    </span>
                  )}
                </div>
                <div className="mt-1">
                  {cfg ? (
                    <span className="text-xs text-green-400">Configured</span>
                  ) : (
                    <span className="text-xs text-gray-500">Not configured</span>
                  )}
                </div>
                {cfg?.credential_hint && (
                  <div className="text-xs text-gray-500 mt-0.5 truncate">
                    {cfg.credential_hint}
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Right column: form */}
        <div className="flex-1 min-w-0">
          {currentDef && selectedProvider ? (
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 space-y-5">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-white">
                  {currentDef.name}
                </h2>
                {isConfigured && (
                  <span className="text-xs text-green-400 bg-green-400/10 px-2 py-1 rounded">
                    Configured
                  </span>
                )}
              </div>

              <div className="text-xs text-gray-500">
                Model prefix: <code className="text-gray-400">{currentDef.model_prefix}</code>
              </div>

              {/* Dynamic fields */}
              {currentDef.fields.map((field) => (
                <div key={field.key}>
                  <label className="block text-sm text-gray-300 mb-1.5">
                    {field.label}
                    {field.required && <span className="text-red-400 ml-1">*</span>}
                  </label>
                  {field.type === 'textarea' ? (
                    <textarea
                      value={formValues[field.key] || ''}
                      onChange={(e) => handleFieldChange(field.key, e.target.value)}
                      placeholder={
                        isConfigured && field.secret
                          ? 'Leave blank to keep existing value'
                          : field.placeholder || ''
                      }
                      rows={3}
                      className="w-full px-4 py-3 bg-gray-800 border border-gray-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 resize-none text-sm"
                      disabled={saving}
                    />
                  ) : (
                    <input
                      type={field.secret ? 'password' : 'text'}
                      value={formValues[field.key] || ''}
                      onChange={(e) => handleFieldChange(field.key, e.target.value)}
                      placeholder={
                        isConfigured && field.secret
                          ? 'Leave blank to keep existing value'
                          : field.placeholder || ''
                      }
                      className="w-full px-4 py-3 bg-gray-800 border border-gray-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 text-sm"
                      disabled={saving}
                    />
                  )}
                </div>
              ))}

              {/* Extra fields from config */}
              {currentConfig &&
                Object.keys(currentConfig.extra_fields).length > 0 && (
                  <div className="space-y-3">
                    <div className="text-gray-500 text-xs uppercase tracking-wider">
                      Additional Settings
                    </div>
                    {Object.entries(currentConfig.extra_fields).map(
                      ([key]) => (
                        <div key={key}>
                          <label className="block text-sm text-gray-300 mb-1.5">
                            {key}
                          </label>
                          <input
                            type="text"
                            value={extraFields[key] || ''}
                            onChange={(e) =>
                              handleExtraFieldChange(key, e.target.value)
                            }
                            className="w-full px-4 py-3 bg-gray-800 border border-gray-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 text-sm"
                            disabled={saving}
                          />
                        </div>
                      )
                    )}
                  </div>
                )}

              {/* Action buttons */}
              <div className="flex flex-wrap gap-3 pt-2">
                <button
                  onClick={handleTest}
                  disabled={testing || saving}
                  className="px-4 py-2 text-sm bg-gray-800 border border-gray-600 hover:border-gray-400 rounded-lg text-gray-300 hover:text-white transition-colors disabled:opacity-50"
                >
                  {testing ? 'Testing...' : 'Test Connection'}
                </button>

                <button
                  onClick={handleSaveClick}
                  disabled={saving}
                  className="px-4 py-2 text-sm bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition-colors"
                >
                  {saving ? 'Saving...' : isConfigured ? 'Update' : 'Save'}
                </button>

                {isConfigured && (
                  <>
                    {!currentConfig?.is_default && (
                      <button
                        onClick={handleSetDefault}
                        disabled={saving}
                        className="px-4 py-2 text-sm bg-gray-800 border border-gray-600 hover:border-purple-500 rounded-lg text-gray-300 hover:text-purple-300 transition-colors disabled:opacity-50"
                      >
                        Set as Default
                      </button>
                    )}

                    <button
                      onClick={handleDelete}
                      disabled={saving}
                      className={`px-4 py-2 text-sm rounded-lg font-medium transition-colors disabled:opacity-50 ${
                        confirmDelete
                          ? 'bg-red-600 hover:bg-red-700 text-white'
                          : 'bg-gray-800 border border-gray-600 hover:border-red-500 text-gray-300 hover:text-red-400'
                      }`}
                    >
                      {confirmDelete ? 'Confirm Remove' : 'Remove'}
                    </button>
                  </>
                )}
              </div>

              {/* Feedback messages */}
              {testResult && (
                <p
                  className={`text-sm ${
                    testResult.ok ? 'text-green-400' : 'text-red-400'
                  }`}
                >
                  {testResult.message}
                </p>
              )}
              {error && <p className="text-sm text-red-400">{error}</p>}
              {successMsg && (
                <p className="text-sm text-green-400">{successMsg}</p>
              )}
            </div>
          ) : (
            <div className="text-gray-500 text-center mt-20">
              Select a provider from the list
            </div>
          )}
        </div>
      </div>

      <PasswordModal
        open={passwordModalOpen}
        onConfirm={handlePasswordConfirm}
        onCancel={() => {
          setPasswordModalOpen(false);
          setPendingAction(null);
        }}
      />
    </div>
  );
}
