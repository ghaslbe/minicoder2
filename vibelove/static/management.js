(() => {
    const byId = id => document.getElementById(id);
    let profiles = [];
    let profileId = null;
    let profileSnapshot = '';
    let skillSnapshot = '';
    let skillIdentity = null;
    let skillsProject = null;
    let profileProject = null;
    let managementBusy = false;

    async function api(path, data) {
        const response = await fetch(path, data === undefined ? {} : {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Anfrage fehlgeschlagen.');
        return result;
    }

    function profileValue() {
        return { name: byId('profileName').value, model: byId('profileModel').value,
            base_url: byId('profileUrl').value, api_key: byId('profileKey').value,
            clear_api_key: byId('profileClearKey').checked,
            max_steps: byId('profileSteps').value, max_tokens: byId('profileTokens').value };
    }
    function skillValue() {
        return { name: byId('skillName').value, scope: byId('skillScope').value,
            content: byId('skillContent').value, project: skillsProject };
    }
    function profileDirty() { return profileSnapshot && JSON.stringify(profileValue()) !== profileSnapshot; }
    function skillDirty() { return skillSnapshot && JSON.stringify(skillValue()) !== skillSnapshot; }
    function discard(dirty) { return !managementBusy && (!dirty || confirm('Ungespeicherte Änderungen verwerfen?')); }

    async function action(statusId, operation) {
        if (managementBusy) return;
        managementBusy = true;
        byId(statusId).textContent = '';
        const buttons = document.querySelectorAll('.manage-view button');
        buttons.forEach(button => { button.disabled = true; });
        try { await operation(); }
        catch (error) { byId(statusId).textContent = error.message; }
        finally {
            managementBusy = false;
            buttons.forEach(button => { button.disabled = false; });
            byId('deleteProfile').disabled = !profileId;
            byId('deleteSkill').disabled = !skillIdentity;
            byId('useSkill').disabled = !skillIdentity;
        }
    }

    function editProfile(profile = null) {
        profileId = profile?.id || null;
        byId('profileName').value = profile?.name || '';
        byId('profileModel').value = profile?.model || '';
        byId('profileUrl').value = profile?.base_url || 'http://localhost:1234/v1';
        byId('profileKey').value = '';
        byId('profileKey').placeholder = profile?.api_key_gesetzt ? 'Neuen Key zum Ersetzen eingeben' : 'API-Key eingeben';
        byId('profileKeyPreview').textContent = profile?.api_key_gesetzt
            ? 'Hinterlegt: ' + profile.api_key_masked : 'Kein Key hinterlegt';
        byId('profileClearKey').checked = false;
        byId('profileSteps').value = profile?.max_steps || 200;
        byId('profileTokens').value = profile?.max_tokens || 16000;
        byId('deleteProfile').disabled = !profileId;
        byId('profileStatus').textContent = '';
        profileSnapshot = JSON.stringify(profileValue());
        document.querySelectorAll('#profileList button').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.id === profileId));
        });
    }

    async function loadProfiles(preferred) {
        const data = await api('/profiles');
        profiles = data.profiles;
        byId('profileList').replaceChildren();
        profiles.forEach(profile => {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.id = profile.id;
            button.textContent = profile.name;
            button.onclick = () => { if (discard(profileDirty())) editProfile(profile); };
            byId('profileList').appendChild(button);
        });
        editProfile(profiles.find(profile => profile.id === (preferred || data.selected)) || profiles[0]);
        return data;
    }

    function editSkill(skill = null) {
        skillIdentity = skill ? { name: skill.name, scope: skill.scope } : null;
        byId('skillName').value = skill?.name || '';
        byId('skillName').disabled = !!skill;
        byId('skillScope').value = skill?.scope || 'project';
        byId('skillScope').disabled = !!skill;
        byId('skillContent').value = skill?.content || '---\ncheck: true\nbeschreibung: \n---\n\n$ARGUMENTS\n';
        byId('deleteSkill').disabled = !skill;
        byId('useSkill').disabled = !skill;
        byId('skillStatus').textContent = '';
        skillSnapshot = JSON.stringify(skillValue());
        document.querySelectorAll('#skillList button').forEach(button => {
            button.setAttribute('aria-pressed', String(!!skill && button.dataset.name === skill.name && button.dataset.scope === skill.scope));
        });
    }

    async function loadSkills(preferred) {
        const data = await api('/skills');
        skillsProject = data.project;
        byId('skillProject').textContent = data.project;
        byId('skillList').replaceChildren();
        data.skills.forEach(skill => {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.name = skill.name;
            button.dataset.scope = skill.scope;
            const scopeLabel = skill.scope === 'shared' ? 'Vibelove' : skill.scope === 'global' ? 'Global' : data.project;
            button.textContent = skill.name + ' · ' + scopeLabel;
            button.title = skill.description;
            button.onclick = () => { if (discard(skillDirty())) editSkill(skill); };
            byId('skillList').appendChild(button);
        });
        if (!data.skills.length) byId('skillList').textContent = 'Keine Skills vorhanden.';
        editSkill(data.skills.find(skill => skill.name === preferred?.name && skill.scope === preferred?.scope) || null);
    }

    async function showView(name) {
        if (name === 'skills' && skillsProject && skillsProject !== layoutProject && !discard(skillDirty())) return;
        document.querySelectorAll('.app-view').forEach(view => { view.hidden = view.id !== name + 'View'; });
        document.querySelectorAll('#appNav button').forEach(button => {
            if (button.dataset.view === name) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
        });
        try {
            if (name === 'setup' && !profileSnapshot) await loadProfiles();
            if (name === 'skills' && (!skillSnapshot || skillsProject !== layoutProject)) await loadSkills();
            if (name === 'build') applyChatLayout();
        } catch (error) { byId(name === 'setup' ? 'profileStatus' : 'skillStatus').textContent = error.message; }
    }
    document.querySelectorAll('#appNav button').forEach(button => { button.onclick = () => showView(button.dataset.view); });
    byId('newProfile').onclick = () => { if (discard(profileDirty())) editProfile(); };
    byId('newSkill').onclick = () => { if (discard(skillDirty())) editSkill(); };
    byId('profileForm').onsubmit = event => {
        event.preventDefault();
        action('profileStatus', async () => {
            const data = await api('/profiles', { id: profileId, ...profileValue() });
            await loadProfiles(data.id);
            byId('profileStatus').textContent = 'Profil gespeichert.';
        });
    };
    byId('deleteProfile').onclick = () => {
        if (!profileId || !confirm('Dieses Modellprofil löschen?')) return;
        action('profileStatus', async () => {
            await api('/profiles', { id: profileId, delete: true });
            await loadProfiles();
            byId('profileStatus').textContent = 'Profil gelöscht.';
        });
    };
    byId('skillForm').onsubmit = event => {
        event.preventDefault();
        action('skillStatus', async () => {
            const value = skillValue();
            await api('/skills', { ...value, create: !skillIdentity });
            await loadSkills(value);
            byId('skillStatus').textContent = 'Skill gespeichert.';
        });
    };
    byId('deleteSkill').onclick = () => {
        if (!skillIdentity || !confirm('Diesen Skill löschen?')) return;
        action('skillStatus', async () => {
            await api('/skills', { ...skillIdentity, project: skillsProject, delete: true });
            await loadSkills();
            byId('skillStatus').textContent = 'Skill gelöscht.';
        });
    };
    byId('useSkill').onclick = async () => {
        if (!skillIdentity || skillDirty()) { byId('skillStatus').textContent = 'Änderungen zuerst speichern.'; return; }
        if (instructionInput.value.trim() && !confirm('Aktuelle Chateingabe ersetzen?')) return;
        await showView('build');
        if (chatLayout.collapsed) { chatLayout.collapsed = false; applyChatLayout(); saveChatLayout(); }
        instructionInput.value = '/' + skillIdentity.name.replace(/\.(md|txt)$/, '') + ' ';
        autoResizeTextarea();
        instructionInput.focus();
    };

    function profileDetails() {
        const profile = profiles.find(item => item.id === byId('projectProfileSelect').value);
        byId('projectProfileDetails').textContent = profile ?
            `${profile.model} · ${profile.max_steps} Schritte · ${profile.max_tokens} Tokens` : '';
    }
    byId('settingsButton').onclick = async () => {
        byId('projectProfileModal').classList.remove('hidden');
        byId('projectProfileStatus').textContent = '';
        try {
            const data = await api('/profiles');
            profiles = data.profiles;
            profileProject = data.project;
            byId('projectProfileSelect').replaceChildren();
            profiles.forEach(profile => {
                const option = document.createElement('option');
                option.value = profile.id; option.textContent = profile.name;
                byId('projectProfileSelect').appendChild(option);
            });
            byId('projectProfileSelect').value = data.selected;
            profileDetails();
        } catch (error) { byId('projectProfileStatus').textContent = error.message; }
    };
    byId('projectProfileSelect').onchange = profileDetails;
    byId('closeProjectProfile').onclick = () => byId('projectProfileModal').classList.add('hidden');
    byId('projectProfileModal').onclick = event => {
        if (event.target === byId('projectProfileModal')) byId('closeProjectProfile').click();
    };
    byId('projectProfileForm').onsubmit = async event => {
        event.preventDefault();
        try {
            await api('/projects/profile', { project: profileProject, id: byId('projectProfileSelect').value });
            byId('closeProjectProfile').click();
        } catch (error) { byId('projectProfileStatus').textContent = error.message; }
    };
    window.addEventListener('beforeunload', event => {
        if (profileDirty() || skillDirty() || managementBusy) { event.preventDefault(); event.returnValue = ''; }
    });
    window.lucide?.createIcons();
})();
