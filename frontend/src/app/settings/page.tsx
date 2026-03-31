'use client';

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { Navbar } from '@/components/Navbar';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  getProviderRegistry,
  getProviders,
  getProviderModels,
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
import type { ChatModel, ProviderDefinition, ProviderConfig } from '@/lib/types';

interface ModelOption {
  id: string;
  name: string;
}

function toChatModel(model: ModelOption): ChatModel {
  return {
    id: model.id,
    name: model.name,
    context_length: 0,
    pricing_prompt: '0',
    pricing_completion: '0',
  };
}

function mergeModels(preferred: ModelOption[], live: ChatModel[]): ChatModel[] {
  const merged = new Map<string, ChatModel>();
  for (const model of preferred) {
    merged.set(model.id, toChatModel(model));
  }
  for (const model of live) {
    merged.set(model.id, model);
  }
  return Array.from(merged.values());
}

function ModelSearch({
  models,
  loading,
  value,
  onChange,
  disabled,
  placeholder,
}: {
  models: ChatModel[];
  loading: boolean;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleOutsideClick(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, []);

  const filtered = query
    ? models.filter(
        (model) =>
          model.name.toLowerCase().includes(query.toLowerCase()) ||
          model.id.toLowerCase().includes(query.toLowerCase())
      )
    : models;
  const shown = filtered.slice(0, 50);
  const selectedModel = value ? models.find((model) => model.id === value) : null;
  const displayValue = open ? query : selectedModel?.name || value || '';

  return (
    <div ref={ref} className="relative space-y-2">
      <Label>Model</Label>
      {loading ? (
        <p className="text-xs text-muted-foreground">Loading models...</p>
      ) : (
        <>
          <Input
            type="text"
            value={displayValue}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
              if (!event.target.value) onChange('');
            }}
            onFocus={() => {
              setQuery(selectedModel?.name || value || '');
              setOpen(true);
            }}
            placeholder={placeholder || 'Search models...'}
            disabled={disabled}
          />
          {open && shown.length > 0 && (
            <div className="absolute z-50 mt-1 w-full max-h-64 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg">
              {shown.map((model) => (
                <button
                  key={model.id}
                  type="button"
                  onClick={() => {
                    onChange(model.id);
                    setQuery(model.name);
                    setOpen(false);
                  }}
                  className={`w-full px-4 py-2 text-left text-sm transition-colors hover:bg-accent ${
                    model.id === value ? 'text-primary' : 'text-foreground'
                  }`}
                >
                  <span className="font-medium">{model.name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{model.id}</span>
                </button>
              ))}
              {filtered.length > 50 && (
                <p className="px-4 py-2 text-xs text-muted-foreground">
                  {filtered.length - 50} more and type to narrow
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LLMProviderSection({
  registry,
  configs,
  getToken,
  onRefresh,
}: {
  registry: Record<string, ProviderDefinition>;
  configs: ProviderConfig[];
  getToken: () => Promise<string | null>;
  onRefresh: () => Promise<void>;
}) {
  const providerKeys = useMemo(() => Object.keys(registry), [registry]);
  const defaultConfig = configs.find((config) => config.is_default) || null;
  const [selectedProvider, setSelectedProvider] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [hasValidatedKey, setHasValidatedKey] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [models, setModels] = useState<ChatModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    if (providerKeys.length === 0 || selectedProvider) return;
    setSelectedProvider(defaultConfig?.provider || providerKeys[0]);
  }, [providerKeys, selectedProvider, defaultConfig]);

  const currentDef = selectedProvider ? registry[selectedProvider] : null;
  const currentConfig = configs.find((config) => config.provider === selectedProvider) || null;
  const curatedModels = useMemo(() => currentDef?.models || [], [currentDef]);
  const isConfigured = !!currentConfig;

  useEffect(() => {
    if (!selectedProvider || !currentDef) return;
    setApiKey('');
    setModels(curatedModels.map(toChatModel));
    setModel(currentConfig?.extra_fields?.model || curatedModels[0]?.id || '');
    setIsEditing(!currentConfig);
    setHasValidatedKey(!!currentConfig);
    setModelsLoading(false);
    setTestResult(null);
    setError(null);
    setSuccessMsg(null);
  }, [selectedProvider, currentDef, currentConfig, curatedModels]);

  useEffect(() => {
    if (!selectedProvider || !currentDef) return;
    if (!currentConfig && selectedProvider !== 'openrouter') return;

    let cancelled = false;

    async function loadLiveModels() {
      setModelsLoading(true);
      const token = await getToken();
      const liveModels = await getProviderModels(selectedProvider, token);
      if (!cancelled) {
        setModels(mergeModels(curatedModels, liveModels));
        setModelsLoading(false);
      }
    }

    loadLiveModels();
    return () => {
      cancelled = true;
    };
  }, [selectedProvider, currentDef, currentConfig, curatedModels, getToken]);

  async function handleValidate() {
    if (!selectedProvider) return;
    if (!apiKey) return;

    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      const token = await getToken();
      const result = await testProvider(
        selectedProvider,
        { credentials: { api_key: apiKey }, extra_fields: {} },
        token
      );
      if (result.models?.length) {
        setModels(mergeModels(curatedModels, result.models));
      }
      setHasValidatedKey(true);
      setTestResult({ ok: true, message: result.message || 'Connection successful' });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Validation failed';
      setHasValidatedKey(false);
      setTestResult({ ok: false, message });
      setError(message);
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    if (!selectedProvider) return;
    const needsValidatedNewKey = !!apiKey;
    if (!isConfigured && !hasValidatedKey) return;
    if (needsValidatedNewKey && !hasValidatedKey) return;

    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      if (apiKey) credentials.api_key = apiKey;
      const extra_fields: Record<string, string> = {};
      if (model) extra_fields.model = model;

      if (isConfigured) {
        await updateProvider(
          selectedProvider,
          {
            credentials: Object.keys(credentials).length ? credentials : undefined,
            extra_fields,
          },
          token
        );
      } else {
        await saveProvider({ provider: selectedProvider, credentials, extra_fields }, token);
      }
      setSuccessMsg('Saved');
      await onRefresh();
      setIsEditing(false);
      setHasValidatedKey(true);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Save failed';
      setError(message);
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove() {
    if (!selectedProvider || !isConfigured) return;
    setSaving(true);
    setError(null);
    try {
      const token = await getToken();
      await deleteProvider(selectedProvider, token);
      setSuccessMsg('Removed');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleMakeDefault() {
    if (!selectedProvider || !isConfigured || currentConfig?.is_default) return;
    setSaving(true);
    setError(null);
    try {
      const token = await getToken();
      await setDefaultProvider(selectedProvider, token);
      setSuccessMsg('Default provider updated');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Set default failed');
    } finally {
      setSaving(false);
    }
  }

  if (providerKeys.length === 0) return null;

  const modelPlaceholder = curatedModels[0]?.id
    ? `Search models... (default: ${curatedModels[0].id})`
    : 'Search models...';
  const configuredModelId = currentConfig?.extra_fields?.model || curatedModels[0]?.id || '';
  const configuredModelName = models.find((entry) => entry.id === configuredModelId)?.name || configuredModelId || 'Not set';
  const canSelectModel = isConfigured ? (!apiKey || hasValidatedKey) : hasValidatedKey;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-foreground">AI Provider</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Pick one provider, add your API key, and choose the model you want the app to use.
        </p>
      </div>

      <p className="rounded-lg border border-border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">
        Configure one provider to get started. OpenRouter also supports BYOK for other model
        providers.{' '}
        <a
          href="https://openrouter.ai/docs/guides/overview/auth/byok"
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:underline"
        >
          BYOK docs
        </a>
        .
      </p>

      <div className="space-y-2">
        <Label>Provider</Label>
        <select
          value={selectedProvider}
          onChange={(event) => setSelectedProvider(event.target.value)}
          disabled={saving}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
        >
          {providerKeys.map((key) => {
            const configured = configs.find((config) => config.provider === key);
            return (
              <option key={key} value={key}>
                {registry[key].name}{configured ? ' (configured)' : ''}
              </option>
            );
          })}
        </select>
      </div>

      {isConfigured && (
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
          <span className="text-xs text-green-500">
            Default provider: {registry[defaultConfig?.provider || selectedProvider]?.name || currentDef?.name || selectedProvider}
          </span>
        </div>
      )}

      {isConfigured && !isEditing ? (
        <>
          <div className="space-y-3 rounded-lg border border-border bg-card px-4 py-4">
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Provider</p>
              <p className="text-sm text-foreground">{currentDef?.name || selectedProvider}</p>
            </div>
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">API Key</p>
              <p className="text-sm text-foreground">{currentConfig?.credential_hint || 'Not saved'}</p>
            </div>
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Model</p>
              <p className="text-sm text-foreground">{configuredModelName}</p>
            </div>
          </div>

          <div className="flex flex-wrap gap-3 pt-1">
            <Button size="sm" onClick={() => setIsEditing(true)} disabled={saving}>
              Update Provider
            </Button>
            {isConfigured && !currentConfig?.is_default && (
              <Button variant="outline" size="sm" onClick={handleMakeDefault} disabled={saving}>
                Set as Default
              </Button>
            )}
            <Button variant="destructive" size="sm" onClick={handleRemove} disabled={saving}>
              Delete
            </Button>
          </div>
        </>
      ) : (
        <>
          <div className="space-y-2">
            <Label>
              API Key <span className="text-destructive">*</span>
            </Label>
            <Input
              type="password"
              value={apiKey}
              onChange={(event) => {
                const nextValue = event.target.value;
                setApiKey(nextValue);
                if (nextValue) {
                  setHasValidatedKey(false);
                } else {
                  setHasValidatedKey(!!currentConfig);
                }
                setTestResult(null);
                setError(null);
              }}
              placeholder={
                isConfigured ? 'Leave blank to keep existing' : (currentDef?.fields?.[0]?.placeholder || '')
              }
              disabled={saving}
            />
          </div>

          <div className="flex flex-wrap gap-3 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={handleValidate}
              disabled={testing || saving || !apiKey}
            >
              {testing ? 'Validating...' : 'Validate'}
            </Button>
          </div>

          <ModelSearch
            models={models}
            loading={modelsLoading}
            value={model}
            onChange={setModel}
            disabled={saving || !canSelectModel}
            placeholder={modelPlaceholder}
          />
          {!canSelectModel && (
            <p className="text-xs text-muted-foreground">
              Validate the API key first to unlock model selection.
            </p>
          )}

          <div className="flex flex-wrap gap-3 pt-1">
            <Button
              size="sm"
              onClick={handleSave}
              disabled={saving || (!isConfigured && !hasValidatedKey) || (!!apiKey && !hasValidatedKey)}
            >
              {saving ? 'Saving...' : isConfigured ? 'Save' : 'Save'}
            </Button>
            {isConfigured && (
              <Button variant="outline" size="sm" onClick={() => setIsEditing(false)} disabled={saving}>
                Cancel
              </Button>
            )}
            {isConfigured && (
              <Button variant="destructive" size="sm" onClick={handleRemove} disabled={saving}>
                Delete
              </Button>
            )}
          </div>
        </>
      )}

      {testResult && (
        <p className={`text-sm ${testResult.ok ? 'text-green-500' : 'text-destructive'}`}>
          {testResult.message}
        </p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {successMsg && <p className="text-sm text-green-500">{successMsg}</p>}
    </div>
  );
}

function SearchProviderSection({
  registry,
  configs,
  getToken,
  onRefresh,
}: {
  registry: Record<string, ProviderDefinition>;
  configs: ProviderConfig[];
  getToken: () => Promise<string | null>;
  onRefresh: () => Promise<void>;
}) {
  const providerKeys = useMemo(() => Object.keys(registry), [registry]);
  const defaultConfig = configs.find((c) => c.is_default);
  const defaultProvider = defaultConfig?.provider ?? '';
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    if (providerKeys.length === 0) return;
    if (!selectedProvider) {
      setSelectedProvider(
        defaultProvider || (providerKeys.includes('duckduckgo') ? 'duckduckgo' : providerKeys[0])
      );
    }
  }, [providerKeys, defaultProvider, selectedProvider]);

  useEffect(() => {
    if (!selectedProvider) return;
    const def = registry[selectedProvider];
    if (def) {
      const vals: Record<string, string> = {};
      for (const field of def.fields) vals[field.key] = '';
      setFormValues(vals);
    }
    setTestResult(null);
    setError(null);
    setSuccessMsg(null);
  }, [selectedProvider, registry]);

  const currentDef = selectedProvider ? registry[selectedProvider] : null;
  const currentConfig = configs.find((c) => c.provider === selectedProvider);
  const isConfigured = !!currentConfig;
  const isDuckDuckGo = selectedProvider === 'duckduckgo';
  const hasFields = currentDef ? currentDef.fields.length > 0 : false;

  async function handleTest() {
    if (!selectedProvider || !currentDef) return;
    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      for (const field of currentDef.fields) {
        if (formValues[field.key]) credentials[field.key] = formValues[field.key];
      }
      await testSearchProvider(selectedProvider, { credentials, extra_fields: {} }, token);
      setTestResult({ ok: true, message: 'Connection successful' });
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : 'Test failed' });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    if (!selectedProvider || !currentDef) return;
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      for (const field of currentDef.fields) {
        if (formValues[field.key]) credentials[field.key] = formValues[field.key];
      }
      if (isConfigured) {
        await updateSearchProvider(selectedProvider, { credentials, extra_fields: {} }, token);
      } else {
        await saveSearchProvider({ provider: selectedProvider, credentials, extra_fields: {} }, token);
      }
      if (!currentConfig?.is_default) {
        await setDefaultSearchProvider(selectedProvider, token);
      }
      setSuccessMsg('Saved');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove() {
    if (!selectedProvider || !isConfigured) return;
    setSaving(true);
    setError(null);
    try {
      const token = await getToken();
      await deleteSearchProvider(selectedProvider, token);
      setSuccessMsg('Removed');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed');
    } finally {
      setSaving(false);
    }
  }

  if (providerKeys.length === 0) return null;

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-foreground">Search Provider</h2>

      <p className="rounded-lg border border-border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">
        DuckDuckGo is enabled by default as a last fallback option. Add as many providers as you
        want. Fallbacks kick in automatically.
      </p>

      <div className="space-y-2">
        <Label>Provider</Label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {providerKeys.map((key) => {
            const configured = configs.find((c) => c.provider === key);
            const def = registry[key];
            const noFieldsRequired = def && def.fields.length === 0;
            const isActive = !!configured || noFieldsRequired;
            const isSelected = selectedProvider === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSelectedProvider(key)}
                disabled={saving}
                className={`relative rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                  isSelected
                    ? 'border-primary bg-primary/5 text-foreground'
                    : 'border-border bg-card text-foreground hover:bg-accent'
                } disabled:opacity-50`}
              >
                <span className="font-medium">{registry[key].name}</span>
                {isActive && (
                  <span className="absolute right-1.5 top-1.5 inline-block h-1.5 w-1.5 rounded-full bg-green-500" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {isConfigured ? (
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
          <span className="text-xs text-green-500">
            Active{currentConfig?.credential_hint ? ` \u2014 ${currentConfig.credential_hint}` : ''}
          </span>
        </div>
      ) : currentDef && !hasFields ? (
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
          <span className="text-xs text-green-500">Enabled by default</span>
        </div>
      ) : null}

      {currentDef && !hasFields && (
        <p className="rounded-lg border border-border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">
          {isDuckDuckGo
            ? 'No API key required. DuckDuckGo is available immediately and works without authentication.'
            : 'No API key required and works without authentication.'}
        </p>
      )}

      {currentDef?.fields.map((field) => (
        <div key={field.key} className="space-y-2">
          <Label>
            {field.label}
            {field.required && <span className="ml-1 text-destructive">*</span>}
          </Label>
          <Input
            type={field.secret ? 'password' : 'text'}
            value={formValues[field.key] || ''}
            onChange={(event) => setFormValues((prev) => ({ ...prev, [field.key]: event.target.value }))}
            placeholder={isConfigured && field.secret ? 'Leave blank to keep existing' : field.placeholder || ''}
            disabled={saving}
          />
        </div>
      ))}

      <div className="flex flex-wrap gap-3 pt-1">
        {hasFields && (
          <Button variant="outline" size="sm" onClick={handleTest} disabled={testing || saving}>
            {testing ? 'Testing...' : 'Test'}
          </Button>
        )}
        <Button size="sm" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : isConfigured ? 'Update' : 'Save'}
        </Button>
        {isConfigured && !isDuckDuckGo && (
          <Button variant="destructive" size="sm" onClick={handleRemove} disabled={saving}>
            Remove
          </Button>
        )}
      </div>

      {testResult && (
        <p className={`text-sm ${testResult.ok ? 'text-green-500' : 'text-destructive'}`}>
          {testResult.message}
        </p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {successMsg && <p className="text-sm text-green-500">{successMsg}</p>}
    </div>
  );
}

function AccountSection({ email }: { email: string | null }) {
  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-foreground">Account</h2>

      <div className="space-y-2">
        <Label>Email</Label>
        <Input type="email" value={email || ''} disabled />
      </div>

      <p className="text-sm text-muted-foreground">More account settings coming soon.</p>
    </div>
  );
}

export default function SettingsPage() {
  const router = useRouter();
  const { getToken, isSignedIn, isLoaded, userEmail } = useAuth();

  const [llmRegistry, setLlmRegistry] = useState<Record<string, ProviderDefinition>>({});
  const [llmConfigs, setLlmConfigs] = useState<ProviderConfig[]>([]);
  const [searchRegistry, setSearchRegistry] = useState<Record<string, ProviderDefinition>>({});
  const [searchConfigs, setSearchConfigs] = useState<ProviderConfig[]>([]);
  const [loadingData, setLoadingData] = useState(true);

  const fetchData = useCallback(async () => {
    const token = await getToken();
    const [llmReg, llmCfgs, searchReg, searchCfgs] = await Promise.all([
      getProviderRegistry(token),
      getProviders(token),
      getSearchProviderRegistry(token),
      getSearchProviders(token),
    ]);

    const llmProviders =
      (llmReg as Record<string, unknown>).providers as Record<string, ProviderDefinition> | undefined;
    const searchProviders =
      (searchReg as Record<string, unknown>).providers as Record<string, ProviderDefinition> | undefined;

    setLlmRegistry(llmProviders || llmReg);
    setLlmConfigs(llmCfgs);
    setSearchRegistry(searchProviders || searchReg);
    setSearchConfigs(searchCfgs);
    setLoadingData(false);
  }, [getToken]);

  useEffect(() => {
    if (isLoaded && !isSignedIn) {
      router.push('/login');
      return;
    }
    if (isLoaded && isSignedIn) {
      async function load() {
        await fetchData();
      }
      void load();
    }
  }, [isLoaded, isSignedIn, router, fetchData]);

  if (!isLoaded || !isSignedIn) {
    return <div className="mt-20 text-center text-muted-foreground">Loading...</div>;
  }
  if (loadingData) {
    return <div className="mt-20 text-center text-muted-foreground">Loading...</div>;
  }

  return (
    <>
      <Navbar />
      <div className="mx-auto max-w-[720px] px-4 py-8">
        <h1 className="mb-6 text-2xl font-semibold">Settings</h1>
        <Tabs defaultValue="ai">
          <TabsList>
            <TabsTrigger value="ai">AI Provider</TabsTrigger>
            <TabsTrigger value="search">Search</TabsTrigger>
            <TabsTrigger value="account">Account</TabsTrigger>
          </TabsList>
          <TabsContent value="ai" className="pt-6">
            <LLMProviderSection
              registry={llmRegistry}
              configs={llmConfigs}
              getToken={getToken}
              onRefresh={fetchData}
            />
          </TabsContent>
          <TabsContent value="search" className="pt-6">
            <SearchProviderSection
              registry={searchRegistry}
              configs={searchConfigs}
              getToken={getToken}
              onRefresh={fetchData}
            />
          </TabsContent>
          <TabsContent value="account" className="pt-6">
            <AccountSection email={userEmail} />
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
