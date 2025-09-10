--
-- PostgreSQL database dump
--

-- Dumped from database version 15.8
-- Dumped by pg_dump version 16.8 (Ubuntu 16.8-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: MediaType; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public."MediaType" AS ENUM (
    'IMAGE',
    'VIDEO'
);


--
-- Name: Role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public."Role" AS ENUM (
    'FREE',
    'BUSINESS',
    'PRO',
    'CORPORATE',
    'ADMIN'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: Account; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."Account" (
    id text NOT NULL,
    "userId" text NOT NULL,
    type text NOT NULL,
    provider text NOT NULL,
    "providerAccountId" text NOT NULL,
    access_token text,
    expires_at integer,
    id_token text,
    refresh_token text,
    scope text,
    session_state text,
    token_type text
);


--
-- Name: Plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."Plans" (
    "planId" text NOT NULL,
    "planName" text NOT NULL
);


--
-- Name: Session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."Session" (
    id text NOT NULL,
    "sessionToken" text NOT NULL,
    "userId" text NOT NULL,
    expires timestamp(3) without time zone NOT NULL
);


--
-- Name: User; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."User" (
    id text NOT NULL,
    email text NOT NULL,
    username text,
    name text,
    fullname text,
    nickname text,
    "avatarUrl" text,
    "coverPhotoUrl" text,
    bio text,
    "emailVerified" timestamp(3) without time zone,
    "createdAt" timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updatedAt" timestamp(3) without time zone NOT NULL,
    "isVerified" boolean DEFAULT false NOT NULL,
    role public."Role" DEFAULT 'FREE'::public."Role" NOT NULL,
    "lastInteraction" timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "passwordHash" text,
    "oauthProvider" text,
    "oauthId" text
);


--
-- Name: UserAISettings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserAISettings" (
    "aiSettingsId" text NOT NULL,
    "userId" text NOT NULL,
    "botSlug" text NOT NULL,
    "ragEnabled" boolean DEFAULT false NOT NULL,
    "aiPersonalityProfile" jsonb NOT NULL
);


--
-- Name: UserBusiness; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserBusiness" (
    "businessId" text NOT NULL,
    "userId" text NOT NULL,
    "businessName" text NOT NULL,
    "businessCategory" text NOT NULL,
    "businessLicense" text NOT NULL,
    "taxId" text NOT NULL,
    "businessWebsite" text
);


--
-- Name: UserFinance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserFinance" (
    "financeId" text NOT NULL,
    "userId" text NOT NULL,
    balance double precision DEFAULT 0.0 NOT NULL,
    currency text NOT NULL,
    "paymentMethods" jsonb NOT NULL,
    "loyaltyPoints" integer DEFAULT 0 NOT NULL
);


--
-- Name: UserLocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserLocations" (
    "locationId" text NOT NULL,
    "userId" text NOT NULL,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    "addressDetail" text NOT NULL,
    "isPrimary" boolean DEFAULT false NOT NULL
);


--
-- Name: UserMedia; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserMedia" (
    "mediaId" text NOT NULL,
    "userId" text NOT NULL,
    "mediaType" public."MediaType" NOT NULL,
    "mediaUrl" text NOT NULL,
    "uploadDate" timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: UserProfile; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserProfile" (
    id text NOT NULL,
    "userId" text NOT NULL,
    bio text,
    "phoneNumber" text,
    tagline text,
    "publicUrlSlug" text,
    "digitalSignature" text
);


--
-- Name: UserSecurity; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserSecurity" (
    id text NOT NULL,
    "userId" text NOT NULL,
    "passwordHash" text,
    "twoFactorEnabled" boolean DEFAULT false NOT NULL,
    "oauthId" text,
    "oauthProvider" text
);


--
-- Name: UserSubscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."UserSubscriptions" (
    "subscriptionId" text NOT NULL,
    "userId" text NOT NULL,
    "planId" text NOT NULL,
    "tokenUsage" integer DEFAULT 0 NOT NULL,
    "tokenLimit" integer NOT NULL,
    "tokenResetAt" timestamp(3) without time zone NOT NULL,
    "subscriptionStart" timestamp(3) without time zone NOT NULL,
    "subscriptionEnd" timestamp(3) without time zone NOT NULL
);


--
-- Name: VerificationToken; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."VerificationToken" (
    identifier text NOT NULL,
    token text NOT NULL,
    expires timestamp(3) without time zone NOT NULL
);


--
-- Name: _prisma_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public._prisma_migrations (
    id character varying(36) NOT NULL,
    checksum character varying(64) NOT NULL,
    finished_at timestamp with time zone,
    migration_name character varying(255) NOT NULL,
    logs text,
    rolled_back_at timestamp with time zone,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_steps_count integer DEFAULT 0 NOT NULL
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id integer NOT NULL,
    user_id character varying(255) NOT NULL,
    message text NOT NULL,
    created_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id integer NOT NULL,
    user_id uuid NOT NULL,
    message text NOT NULL,
    type text NOT NULL,
    status text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: Account Account_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Account"
    ADD CONSTRAINT "Account_pkey" PRIMARY KEY (id);


--
-- Name: Plans Plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Plans"
    ADD CONSTRAINT "Plans_pkey" PRIMARY KEY ("planId");


--
-- Name: Session Session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Session"
    ADD CONSTRAINT "Session_pkey" PRIMARY KEY (id);


--
-- Name: UserAISettings UserAISettings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserAISettings"
    ADD CONSTRAINT "UserAISettings_pkey" PRIMARY KEY ("aiSettingsId");


--
-- Name: UserBusiness UserBusiness_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserBusiness"
    ADD CONSTRAINT "UserBusiness_pkey" PRIMARY KEY ("businessId");


--
-- Name: UserFinance UserFinance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserFinance"
    ADD CONSTRAINT "UserFinance_pkey" PRIMARY KEY ("financeId");


--
-- Name: UserLocations UserLocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserLocations"
    ADD CONSTRAINT "UserLocations_pkey" PRIMARY KEY ("locationId");


--
-- Name: UserMedia UserMedia_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserMedia"
    ADD CONSTRAINT "UserMedia_pkey" PRIMARY KEY ("mediaId");


--
-- Name: UserProfile UserProfile_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserProfile"
    ADD CONSTRAINT "UserProfile_pkey" PRIMARY KEY (id);


--
-- Name: UserSecurity UserSecurity_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserSecurity"
    ADD CONSTRAINT "UserSecurity_pkey" PRIMARY KEY (id);


--
-- Name: UserSubscriptions UserSubscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserSubscriptions"
    ADD CONSTRAINT "UserSubscriptions_pkey" PRIMARY KEY ("subscriptionId");


--
-- Name: User User_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."User"
    ADD CONSTRAINT "User_pkey" PRIMARY KEY (id);


--
-- Name: _prisma_migrations _prisma_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public._prisma_migrations
    ADD CONSTRAINT _prisma_migrations_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: Account_provider_providerAccountId_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "Account_provider_providerAccountId_key" ON public."Account" USING btree (provider, "providerAccountId");


--
-- Name: Session_sessionToken_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "Session_sessionToken_key" ON public."Session" USING btree ("sessionToken");


--
-- Name: UserAISettings_botSlug_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "UserAISettings_botSlug_key" ON public."UserAISettings" USING btree ("botSlug");


--
-- Name: UserProfile_publicUrlSlug_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "UserProfile_publicUrlSlug_key" ON public."UserProfile" USING btree ("publicUrlSlug");


--
-- Name: UserProfile_userId_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "UserProfile_userId_key" ON public."UserProfile" USING btree ("userId");


--
-- Name: UserSecurity_userId_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "UserSecurity_userId_key" ON public."UserSecurity" USING btree ("userId");


--
-- Name: User_email_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "User_email_key" ON public."User" USING btree (email);


--
-- Name: User_username_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "User_username_key" ON public."User" USING btree (username);


--
-- Name: VerificationToken_identifier_token_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "VerificationToken_identifier_token_key" ON public."VerificationToken" USING btree (identifier, token);


--
-- Name: VerificationToken_token_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX "VerificationToken_token_key" ON public."VerificationToken" USING btree (token);


--
-- Name: Account Account_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Account"
    ADD CONSTRAINT "Account_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Session Session_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Session"
    ADD CONSTRAINT "Session_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserAISettings UserAISettings_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserAISettings"
    ADD CONSTRAINT "UserAISettings_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserBusiness UserBusiness_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserBusiness"
    ADD CONSTRAINT "UserBusiness_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserFinance UserFinance_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserFinance"
    ADD CONSTRAINT "UserFinance_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserLocations UserLocations_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserLocations"
    ADD CONSTRAINT "UserLocations_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserMedia UserMedia_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserMedia"
    ADD CONSTRAINT "UserMedia_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserProfile UserProfile_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserProfile"
    ADD CONSTRAINT "UserProfile_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserSecurity UserSecurity_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserSecurity"
    ADD CONSTRAINT "UserSecurity_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserSubscriptions UserSubscriptions_planId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserSubscriptions"
    ADD CONSTRAINT "UserSubscriptions_planId_fkey" FOREIGN KEY ("planId") REFERENCES public."Plans"("planId") ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: UserSubscriptions UserSubscriptions_userId_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."UserSubscriptions"
    ADD CONSTRAINT "UserSubscriptions_userId_fkey" FOREIGN KEY ("userId") REFERENCES public."User"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

