import nextVitals from 'eslint-config-next/core-web-vitals';
// Existing request hooks predate the React compiler effect rule. Keep the
// findings visible as warnings; changing all hooks is outside this UI change.
// New sentiment UI is checked with the default error severity.
const legacy=['AnnualStudy','DataInventory','DragonCoverage','Dragons','FeatureStudy','History','HistoryLimits','NonIpoStudy','NonStScope','ObservedStreaks','Research'];
export default [...nextVitals,{files:legacy.map(n=>`src/components/${n}.tsx`),rules:{'react-hooks/set-state-in-effect':'warn'}}];
