import React, {useEffect, useState, useRef} from 'react';
import {ActivityIndicator, Alert, AppState, Button, Linking, Platform, ScrollView, StyleSheet, Text, View} from 'react-native';
import {SafeAreaProvider, SafeAreaView} from 'react-native-safe-area-context';
import * as Browser from 'expo-web-browser';
import * as SecureStore from 'expo-secure-store';
import {randomUUID} from 'expo-crypto';
import {registerWithOnesa} from './registration.mjs';
import {ORIGIN} from './session.mjs';
import {createOperator} from './operator.mjs';

export default function App() {
  const model = useRef(null);
  const [view, setView] = useState({account:null, items:[], selected:null, busy:false,
    stopping:false, available:false, error:'', message:''});
  useEffect(() => {
    const operator = createOperator({openBrowser:Browser.openAuthSessionAsync, uuid:randomUUID,
      changed:setView, storage:{
        get:key=>SecureStore.getItemAsync(key),
        set:(key,value)=>SecureStore.setItemAsync(key,value,{keychainAccessible:SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY}),
        remove:key=>SecureStore.deleteItemAsync(key)
      }});
    model.current = operator;
    let active = AppState.currentState === 'active', focused = true;
    const visibility = () => operator.foregroundChanged(active && focused);
    visibility();
    const activity = AppState.addEventListener('change', value=>{active=value === 'active';visibility();});
    // Android notification shade/window focus can change without AppState changing.
    const focus = Platform.OS === 'android' ? [
      AppState.addEventListener('blur', ()=>{focused=false;visibility();}),
      AppState.addEventListener('focus', ()=>{focused=true;visibility();})
    ] : [];
    const links = Linking.addEventListener('url', event=>{void operator.link(event.url);});
    void Linking.getInitialURL().then(url=>operator.start(url)).catch(()=>operator.start());
    return () => {activity.remove();focus.forEach(listener=>listener.remove());links.remove();operator.dispose();if(model.current === operator) model.current=null;};
  }, []);
  const {account, items, selected, busy, stopping, available, error, message} = view;
  const [registering, setRegistering] = useState(false);
  const registrationOpen = useRef(false);
  async function register(audience) {
    if (registrationOpen.current) return;
    registrationOpen.current = true;
    setRegistering(true);
    try {
      const origin = process.env.EXPO_PUBLIC_ONESA_REGISTRATION_ORIGIN ||
        (ORIGIN === 'https://robot.onesa.eu' ? 'https://account.onesa.eu' : '');
      if (!origin) throw new Error('Registration is unavailable in this environment.');
      await registerWithOnesa(Browser.openAuthSessionAsync, Platform.OS, audience, () => model.current?.connect(true), origin);
    } catch (error) {
      Alert.alert('Onesa registration', error.message);
    } finally {
      registrationOpen.current = false;
      setRegistering(false);
    }
  }
  function confirmCommand(title, kind, id) {
    const confirm = model.current?.review(kind,id);
    Alert.alert(title,
      'This affects only the software simulator. If Onesa MFA is needed, complete it in the system browser, then review and confirm the operation again.',
      [{text:'Cancel',style:'cancel'}, {text:'Confirm simulation',onPress:()=>{void confirm?.();}}]);
  }
  return <SafeAreaProvider><SafeAreaView style={styles.safe}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.page}>
    <Text accessibilityRole="header" style={styles.title}>Onesa Robot</Text>
    {busy && <ActivityIndicator accessibilityLabel="Working" />}
    {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    {!!message && <Text accessibilityRole="alert" style={styles.verified}>{message}</Text>}
    {!account ? <>
      <Button title="Sign in with Onesa" disabled={busy || registering} onPress={()=>{void model.current?.connect(true);}} />
      <Button title="Create an Onesa account" disabled={busy || registering} onPress={()=>{void register("user");}} />
      <Button title="Request administrator access" disabled={busy || registering} onPress={()=>{void register("admin");}} />
    </> : <>
      <Text style={styles.muted}>{account.name}</Text>
      <View style={styles.stopPanel}>
        <Button title={stopping ? 'STOP request in progress' : 'STOP simulator now'} color="#a32222" disabled={stopping} onPress={()=>{void model.current?.stop();}}/>
        <Text>Independent operator Stop remains available during other requests. No physical actuation is supported.</Text>
      </View>
      <Text accessibilityLiveRegion="polite">{available ? view.observation?.status.stopped ? 'Simulator stopped' : view.observation?.status.active_task ? 'Observing running simulation' : 'Simulator ready' : 'Observation unavailable — simulation controls locked'}</Text>
      <Text style={styles.heading}>Simulator task journal</Text>
      <Text>Current observations renew supervision only while this app is active. The independent backend watchdog stops an unobserved task.</Text>
      {account.role!=='viewer' && <Button title="Propose 0.1 rad simulation check" disabled={busy||stopping||!view.canPropose} onPress={()=>{void model.current?.command('propose');}}/>}
      {selected && <View style={styles.item}><Text selectable>{selected.id}</Text><Text>{selected.state} · {selected.position} rad</Text><Text>{selected.reason}</Text>
        {account.role!=='viewer' && selected.state==='proposed' && <Button title="Approve simulated motion" disabled={busy||stopping||!view.canApprove} onPress={()=>confirmCommand('Approve this simulation proposal?','approve',selected.id)} />}
        {account.role!=='viewer' && ['proposed','running'].includes(selected.state) && <Button title={selected.state==='running'?'Cancel running simulation':'Cancel proposal'} disabled={busy||stopping||!available} onPress={()=>{void model.current?.command('cancel',selected.id);}} />}</View>}
      {!items.length && <Text>{available ? 'No tasks for this operator.' : 'Current task history is unavailable.'}</Text>}
      {items.map(task=><Button key={task.id} title={task.state+' · '+task.id.slice(0,8)} disabled={busy||!available} onPress={()=>{void model.current?.select(task.id);}}/>)}
      {account.role!=='viewer' && <Button title="Reset simulator Stop (MFA required)" disabled={busy||stopping||!view.canReset} onPress={()=>confirmCommand('Reset the simulation Stop latch?','reset')} />}
      <Button title="Refresh" disabled={busy||stopping} onPress={()=>{void model.current?.refresh();}} />
      <Button title="Sign out" disabled={busy} onPress={()=>{void model.current?.logout();}} />
    </>}
  </ScrollView></SafeAreaView></SafeAreaProvider>;
}
const styles = StyleSheet.create({
  safe:{flex:1, backgroundColor:'#fff'},page:{padding:22,gap:14},title:{fontSize:30,fontWeight:'700',color:'#192739'},
  heading:{fontSize:22,fontWeight:'600',color:'#24384e'},muted:{color:'#405365'},error:{color:'#962f2b'},
  item:{paddingVertical:10,gap:4},stopPanel:{gap:8,padding:12,borderColor:'#a32222',borderWidth:2,borderRadius:8},verified:{color:'#12603a'},
});
