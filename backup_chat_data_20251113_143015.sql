--
-- PostgreSQL database dump
--

\restrict pwQGgRJPgJORG39ptQARcqOUOidCGwZFibZcOvdt4i2ub2vaI4OhejsJ5Wt7mLj

-- Dumped from database version 15.8
-- Dumped by pg_dump version 18.0 (Ubuntu 18.0-1.pgdg22.04+3)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: messages; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.messages (id, user_id, message, created_at) FROM stdin;
\.


--
-- Name: messages_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.messages_id_seq', 1, false);


--
-- PostgreSQL database dump complete
--

\unrestrict pwQGgRJPgJORG39ptQARcqOUOidCGwZFibZcOvdt4i2ub2vaI4OhejsJ5Wt7mLj

