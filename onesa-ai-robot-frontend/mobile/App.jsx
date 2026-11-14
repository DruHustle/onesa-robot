import React, {useEffect, useState, useRef} from 'react';
import {ActivityIndicator, Button, ScrollView, StyleSheet, Text, View} from 'react-native';
import {SafeAreaProvider, SafeAreaView} from 'react-native-safe-area-context';
import * as Browser from 'expo-web-browser';
import * as SecureStore from 'expo-secure-store';
import {randomUUID} from 'expo-crypto';
import {call, signIn} from './session.mjs';

export default function App() {
  const [account, setAccount] = useState(null);
  const [csrf, setCsrf] = useState('');
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const target = useRef(null);
  const previousAccount = useRef(null);

  function clear() {
    setAccount(null); setCsrf(''); setSelected(null); setItems([]);
  }
  async function perform(action) {
    if (active.current) return;
    active.current = true; setBusy(true); setError('');
    try { await action(); }
    catch (err) { if(err.status === 401) clear(); setError(err.message); }
    finally { active.current = false; setBusy(false); }
  }
  async function refresh() {

    const result = await call('/tasks');
    setItems(result.tasks);
    if(target.current) setSelected(result.tasks.find(task=>task.id===target.current)||null);

  }
  async function connect(interactive) {
    const grant = interactive ? await signIn(Browser.openAuthSessionAsync) : {};
    const user = await call('/auth/me');
    const key = user.id || user.name;
    if (previousAccount.current && previousAccount.current !== key) {setSelected(null);setItems([]);target.current=null;}
    previousAccount.current = key;
    let protection = user.csrf || grant.csrf || await SecureStore.getItemAsync('csrf');
    if (!protection) throw Error('Start a new sign-in to continue.');
    await SecureStore.setItemAsync('csrf',protection,{keychainAccessible:SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
    setAccount(user); setCsrf(protection);
    await refresh();
  }
  useEffect(() => {perform(() => connect(false));}, []);
  async function logout() {
    await call('/auth/logout', {method:'POST',csrf});
    await SecureStore.deleteItemAsync('csrf');
    clear(); target.current=null; previousAccount.current=null;
  }

  async function propose(){
    const task=await call('/tasks',{method:'POST',csrf,body:{command_id:randomUUID(),issued_at:Date.now()/1000,target_radians:0.1,duration_seconds:2}});
    target.current=task.id; await refresh();
  }
  async function cancel(){await call('/tasks/'+selected.id+'/cancel',{method:'POST',csrf});await refresh();}

  return <SafeAreaProvider><SafeAreaView style={styles.safe}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.page}>
    <Text accessibilityRole="header" style={styles.title}>Onesa Robot</Text>
    {busy && <ActivityIndicator accessibilityLabel="Working" />}
    {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    {!account ? <Button title="Sign in with Onesa" disabled={busy} onPress={()=>perform(()=>connect(true))} /> : <>
      <Text style={styles.muted}>{account.display_name || account.name}</Text>

      <Text style={styles.heading}>Simulator task journal</Text>
      <Text>Review the operator’s recorded simulation tasks. A proposal moves no hardware.</Text>
      {account.role!=='viewer' && <Button title="Propose 0.1 rad simulation check" disabled={busy} onPress={()=>perform(propose)}/>}
      {selected && <View style={styles.item}><Text selectable>{selected.id}</Text><Text>{selected.state} · {selected.position} rad</Text><Text>{selected.reason}</Text>
        {account.role!=='viewer' && selected.state==='proposed' && <Button title="Cancel proposal" disabled={busy} onPress={()=>perform(cancel)} />}</View>}
      {!items.length && <Text>No tasks for this operator.</Text>}
      {items.map(task=><Button key={task.id} title={task.state+' · '+task.id.slice(0,8)} disabled={busy} onPress={()=>{target.current=task.id;setSelected(task);}}/>)}

      <Button title="Refresh" disabled={busy} onPress={()=>perform(refresh)} />
      <Button title="Sign out" disabled={busy} onPress={()=>perform(logout)} />
    </>}
  </ScrollView></SafeAreaView></SafeAreaProvider>;
}
const styles = StyleSheet.create({
  safe:{flex:1, backgroundColor:'#fff'},page:{padding:22,gap:14},title:{fontSize:30,fontWeight:'700',color:'#192739'},
  heading:{fontSize:22,fontWeight:'600',color:'#24384e'},muted:{color:'#405365'},error:{color:'#962f2b'},
  input:{borderWidth:1,borderRadius:8,padding:12,borderColor:'#607386',minHeight:54},item:{paddingVertical:10,gap:4},
});
