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
  getSearchProviderRegistry,
  getSearchProviders,
  saveSearchProvider,
  updateSearchProvider,
  deleteSearchProvider,
  testSearchProvider,
  setDefaultSearchProvider,
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

type ProviderCategory = 'llm' | 'search';

export default function SettingsPage() {
  const router = useRouter();
  const { getToken, isSignedIn, isLoaded } = useAuth();

  // LLM providers
  const [registry, setRegistry] = useState<Record<string, ProviderDefinition>>({});
  const [configs, setConfigs] = useState<ProviderConfig[]>([]);

  // Search providers
  const [searchRegistry, setSearchRegistry] = useState<Record<string, ProviderDefinition>>({});
  const [searchConfigs, setSearchConfigs] = useState<ProviderConfig[]>([]);

  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<ProviderCategory>('llm');
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
    const [reg, cfgs, sReg, sCfgs] = await Promise.all([
      getProviderRegistry(token),
      getProviders(token),
      getSearchProviderRegistry(token),
      getSearchProviders(token),
    ]);
    // Registry endpoints return {providers: {...}} — unwrap if wrapped
    const llmReg = (reg as Record<string, unknown>).providers as Record<string, ProviderDefinition> | undefined;
    setRegistry(llmReg || reg);
    setConfigs(cfgs);
    const searchRegInner = (sReg as Record<string, unknown>).providers as Record<string, ProviderDefinition> | undefined;
    setSearchRegistry(searchRegInner || sReg);
    setSearchConfigs(sCfgs);
    setLoadingData(false);
    return { reg: llmReg || reg, cfgs, sCfgs };
  }, [getToken]);

  useEffect(() => {
    if (isLoaded && !isSignedIn) {
      router.push('/login');
      return;
    }
    if (isLoaded && isSignedIn) {
      fetchData().then(({ reg }) => {
        const keys = Object.keys(reg);
        if (keys.length > 0 && !selectedProvider) {
          setSelectedProvider(keys[0]);
          setSelectedCategory('llm');
        }
      });
    }
  }, [isLoaded, isSignedIn, router, fetchData, selectedProvider]);

  // When selectedProvider changes, reset form
  useEffect(() => {
    if (!selectedProvider) return;
    const activeRegistry = selectedCategory === 'llm' ? registry : searchRegistry;
    const activeConfigs = selectedCategory === 'llm' ? configs : searchConfigs;
    const def = activeRegistry[selectedProvider];
    const cfg = activeConfigs.find((c) => c.provider === selectedProvider);

    if (def) {
      const vals: Record<string, string> = {};
      for (const field of def.fields) {
        vals[field.key] = '';
      }
      setFormValues(vals);
      setExtraFields(cfg?.extra_fields || {});
    }
    setTestResult(null);
    setError(null);
    setSuccessMsg(null);
    setConfirmDelete(false);
  }, [selectedProvider, selectedCategory, registry, searchRegistry, configs, searchConfigs]);

  const providerKeys = Object.keys(registry);
  const searchProviderKeys = Object.keys(searchRegistry);

  const activeRegistry = selectedCategory === 'llm' ? registry : searchRegistry;
  const activeConfigs = selectedCategory === 'llm' ? configs : searchConfigs;
  const currentDef = selectedProvider ? activeRegistry[selectedProvider] : null;
  const currentConfig = selectedProvider
    ? activeConfigs.find((c) => c.provider === selectedProvider)
    : null;
  const isConfigured = !!currentConfig;
  const hasFields = currentDef ? currentDef.fields.length > 0 : false;

  function selectProvider(key: string, category: ProviderCategory) {
    setSelectedProvider(key);
    setSelectedCategory(category);
  }

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
      const testFn = selectedCategory === 'llm' ? testProvider : testSearchProvider;
      await testFn(selectedProvider, { credentials, extra_fields: extraFields }, token);
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

      if (selectedCategory === 'llm') {
        if (pendingAction === 'update') {
          await updateProvider(selectedProvider, { credentials, extra_fields: extraFields, password }, token);
        } else {
          await saveProvider({ provider: selectedProvider, credentials, extra_fields: extraFields, password }, token);
        }
      } else {
        if (pendingAction === 'update') {
          await updateSearchProvider(selectedProvider, { credentials, extra_fields: extraFields, password }, token);
        } else {
          await saveSearchProvider({ provider: selectedProvider, credentials, extra_fields: extraFields, password }, token);
        }
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
      const deleteFn = selectedCategory === 'llm' ? deleteProvider : deleteSearchProvider;
      await deleteFn(selectedProvider, token);
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
      const setDefaultFn = selectedCategory === 'llm' ? setDefaultProvider : setDefaultSearchProvider;
      await setDefaultFn(selectedProvider, token);
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

  function renderSidebarButton(key: string, def: ProviderDefinition, cfg: ProviderConfig | undefined, category: ProviderCategory) {
    const isActive = selectedProvider === key && selectedCategory === category;
    return (
      <button
        key={`${category}-${key}`}
        onClick={() => selectProvider(key, category)}
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
            return renderSidebarButton(key, def, cfg, 'llm');
          })}

          <div className="text-gray-500 text-xs uppercase tracking-wider mb-3 mt-6 pt-4 border-t border-gray-800">
            Search Providers
          </div>
          {searchProviderKeys.map((key) => {
            const def = searchRegistry[key];
            const cfg = searchConfigs.find((c) => c.provider === key);
            return renderSidebarButton(key, def, cfg, 'search');
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

              {currentDef.model_prefix && (
                <div className="text-xs text-gray-500">
                  Model prefix: <code className="text-gray-400">{currentDef.model_prefix}</code>
                </div>
              )}

              {/* No-fields notice for keyless providers (e.g. DuckDuckGo) */}
              {!hasFields && (
                <p className="text-sm text-gray-400 bg-gray-800/50 border border-gray-700 rounded-lg px-4 py-3">
                  No API key required &mdash; this provider works without authentication.
                </p>
              )}

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
                {hasFields && (
                  <button
                    onClick={handleTest}
                    disabled={testing || saving}
                    className="px-4 py-2 text-sm bg-gray-800 border border-gray-600 hover:border-gray-400 rounded-lg text-gray-300 hover:text-white transition-colors disabled:opacity-50"
                  >
                    {testing ? 'Testing...' : 'Test Connection'}
                  </button>
                )}

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
